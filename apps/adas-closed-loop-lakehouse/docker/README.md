# 本地全栈编排

六大组件 + 两个配套件，一条命令起：

| 服务 | 镜像 | 容器端口 | 宿主机发布端口 | 对应 config.py |
|---|---|---|---|---|
| `minio` | `quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z` | 9000 / 9001 | **18600 / 18601** | `MinioConfig` |
| `mysql` | `mysql:8.0.40` | 3306 | **18606** | CDC 源库 + 控制面库 |
| `kafka` | `apache/kafka:3.9.0` | 9092 | **18692** | `KafkaConfig` |
| `jobmanager` | 自建 `adas-lakehouse/flink` | 8081 | **18681** | `FlinkConfig.jobmanager_url` |
| `taskmanager` | 自建 `adas-lakehouse/flink` | — | — | — |
| `sql-gateway` | 自建 `adas-lakehouse/flink` | 8083 | **18683** | `FlinkConfig.sql_gateway_url` |
| `starrocks` | `starrocks/allin1-ubuntu:3.4.2` | 9030 / 8030 / 8040 | **18630 / 18631 / 18640** | `StarRocksConfig` |
| `neo4j` | `neo4j:5.26.0` | 7687 / 7474 | **18687 / 18674** | `Neo4jConfig` |
| `redis` | `redis:7.4.1-alpine` | 6379 | **18679** | `controlplane/store.py` |

宿主机侧一律走 186xx 独占号段（避开同仓平台的 8080/8000/3000/8020/8030/8040/
6379/6333/9200）；**容器端口一律保持组件原生默认值不动**。完整理由与对照表见
[`../docs/ports.md`](../docs/ports.md)。

另有四个跑完即退的 init 容器：`minio-init`（建桶）、`kafka-init`（建 topic）、
`starrocks-init`（建 External Catalog 与 `adas_ads`）、`neo4j-init`（建唯一约束）。
下游服务用 `condition: service_completed_successfully` 等它们。

## 起停

在应用根目录（`apps/adas-closed-loop-lakehouse/`）执行：

```bash
cp .env.example .env
docker compose --env-file .env -f docker/compose.yaml up -d --build

# 看状态（等所有 healthy）
docker compose --env-file .env -f docker/compose.yaml ps

# 停（留数据）
docker compose --env-file .env -f docker/compose.yaml down
# 停并删卷（彻底重来）
docker compose --env-file .env -f docker/compose.yaml down -v
```

`--env-file .env` 不能省：compose 文件在 `docker/` 下，而 `.env` 在应用根目录。
不给 `.env` 也能起 —— compose 里每个变量都写了 `${VAR:-默认值}`，默认值逐字等于
`config.py`，所以裸 `up` 起来的栈和 `.env.example` 完全一致。

## ⚠️ localhost 与服务名

`config.py` 的默认值全是 `localhost:186xx`，它面向**宿主机上跑的 Python**，
逐条等于 compose `ports:` 映射的左半边，所以：

```bash
python -m adas_lakehouse.cli ...   # 宿主机直接跑，零配置连通
```

但**容器内部的 `localhost` 是容器自己**。Docker 不允许把 `localhost` 重定向到别的
容器（容器 `/etc/hosts` 里 `127.0.0.1 localhost` 恒定优先），所以 Flink / StarRocks
访问 MinIO、Kafka、MySQL 必须用服务名。compose 已经在每个容器的 `environment` 里
覆盖好了：

| 变量 | 宿主机口径（config.py 默认） | 容器口径（compose 覆盖） |
|---|---|---|
| `MINIO_ENDPOINT` | `http://localhost:18600` | `http://minio:9000` |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:18692` | `kafka:29092` |
| `CDC_MYSQL_HOSTNAME` / `CDC_MYSQL_PORT` | `localhost` / `18606` | `mysql` / `3306` |

注意两列端口不同不是笔误：左列是宿主机发布端口，右列是容器端口。

**后果**：`ddl/*.sql` 与 `flink/sql/*.sql` 里由 `scripts/export_ddl.py` 渲染进去的
`http://localhost:18600` / `localhost:18692` / `localhost:18606` 是**宿主机口径**，
只在宿主机侧客户端执行时成立。要在容器网络内执行，先用容器口径重新导出：

```bash
MINIO_ENDPOINT=http://minio:9000 \
KAFKA_BOOTSTRAP_SERVERS=kafka:29092 \
CDC_MYSQL_HOSTNAME=mysql \
CDC_MYSQL_PORT=3306 \
python3 scripts/export_ddl.py
```

这段命令用的全是容器端口，端口迁移对它没有任何影响。

StarRocks 那一路不用管 —— `starrocks-init` 建 External Catalog 时用的就是
`http://minio:9000`，开箱即可直查湖。

## 建表与跑作业

```bash
# 1) 湖仓 88 张表（宿主机侧 SQL Client 口径）
docker compose --env-file .env -f docker/compose.yaml exec jobmanager \
  /opt/flink/bin/sql-client.sh -f /opt/adas/ddl/00_catalog.sql

# 2) StarRocks 内表与视图
mysql -h 127.0.0.1 -P 18630 -u root < ddl/starrocks_ingest.sql   # 18630 → 容器 9030

# 3) 入湖作业（通道一 CDC）
docker compose --env-file .env -f docker/compose.yaml exec jobmanager \
  /opt/flink/bin/sql-client.sh -f /opt/adas/flink-sql/ingest_cdc_mysql.sql
```

`../flink/sql` 与 `../ddl` 已经只读挂进 Flink 容器的 `/opt/adas/flink-sql`
和 `/opt/adas/ddl`，改了本地文件容器里立刻可见，不用重建镜像。

SQL 里的 `${MINIO_SECRET_KEY}` / `${CDC_MYSQL_PASSWORD}` 是刻意留的占位符（口令不落盘），
执行前自己替换，或者用 `envsubst < x.sql | sql-client.sh -f -`。

## Flink 镜像里装了什么

`docker/flink/Dockerfile` 在官方 `flink:1.20.1-java17` 上补了四个 connector，
版本全部写死，且用 Maven Central 的 `.sha1` 逐个校验：

| jar | 版本 | 干什么 |
|---|---|---|
| `paimon-flink-1.20` | 1.0.1 | 湖格式本体：catalog / 主键表 / changelog / compaction |
| `paimon-s3` | 1.0.1 | Paimon 侧 S3，`warehouse=s3://...` 靠它 |
| `flink-sql-connector-kafka` | 3.3.0-1.20 | 通道二事件流 + `lineage.change.event` |
| `flink-sql-connector-mysql-cdc` | 3.3.0 | 通道一：全量快照 → 增量 binlog → 断点续传 |
| `flink-shaded-hadoop-2-uber` | 2.8.3-10.0 | Paimon 的硬依赖，见下 |
| `mysql-connector-j` | 8.4.0 | MySQL JDBC 驱动，见下 |

外加把镜像自带的 `flink-s3-fs-hadoop-1.20.1.jar` 从 `opt/` 搬进
`plugins/s3-fs-hadoop/`（Flink 侧 S3，想把 checkpoint 落 MinIO 时用）。

后两个 jar 不是「以防万一」，是实测踩出来的硬依赖，删掉就起不来：

- **`flink-shaded-hadoop-2-uber`**：Paimon 建 catalog 的第一步就要
  `org.apache.hadoop.conf.Configuration`，而 `paimon-flink` 和 `paimon-s3`
  两个 bundle 里 unshaded 的 Hadoop 类是 0 个（`unzip -l` 数过）。缺了它，
  `CREATE CATALOG` 直接 `ClassNotFoundException`。
  `flink-s3-fs-hadoop` 插件里那份 Hadoop 是 shade 过的，而且插件类加载器隔离，
  顶不了这个位。
- **`mysql-connector-j`**：Flink CDC 3.x 因为 GPL 许可已经不把 MySQL 驱动打进
  fat jar 了。缺了它，建 `mysql-cdc` 表报
  `ClassNotFoundException: com.mysql.cj.jdbc.Driver`。
  （GPLv2 + FOSS 例外，对外分发本镜像前自行确认合规。）

**Paimon 版本选型依据**：仓库里的 SQL 只写了 `'type' = 'paimon'` 和
`'metastore' = 'filesystem'`，没有钉版本；`src/` 里也没有任何 Paimon 版本字面量。
按「Paimon 1.x 稳定版」取 1.0.1 —— `catalog/spec.py` 硬校验的那套物理策略
（主键表、`changelog-producer` 分层、bucket 五档）在 Paimon 1.0 已全部 GA。
`paimon-flink-1.20` 适配包与 `FLINK_VERSION` 的 minor 对齐。

改版本：`docker/flink/Dockerfile` 顶部的 `ARG`，或者
`docker compose build --build-arg PAIMON_VERSION=1.0.2 jobmanager`。

## 镜像 tag 拉不下来怎么办

所有 tag 都是写死的具体版本，不用 `latest`。如果某个 tag 在你的镜像源上不存在
（MinIO 的 `RELEASE.*` 日期 tag 尤其容易过期下架），换成邻近的同系列 tag 即可，
compose 里只有那一行需要改。

## 实测记录

这套编排在 macOS + Docker 29.5 上完整跑通过一次，链路是通的：

1. 九个常驻服务全部 `healthy`，四个 init 容器全部 `exit 0`
   （建了 `adas-lakehouse`/`adas-raw` 两个桶、四个 topic、
   `paimon_catalog` External Catalog、五条血缘唯一约束）。
2. Flink SQL Client 建 Paimon catalog → 建主键表 → `INSERT`，
   数据落在 `s3://adas-lakehouse/warehouse`。
3. StarRocks 通过 `paimon_catalog` 读到了**同一份**数据 ——
   「一套存储、双引擎读」是真的通的，不是纸面设计。
4. 通道一 CDC 全链路：MySQL 三张源表 → Flink CDC → Paimon → StarRocks。
   全量快照三行全部入湖；再在 MySQL 做 `UPDATE` + `INSERT`，
   增量 binlog 也在下一个 checkpoint 后到湖里。

Paimon 按 checkpoint 提交，所以从改 MySQL 到 StarRocks 能查到，
要等一个 `FLINK_CHECKPOINT_INTERVAL_MS`（默认 60s）。这是正常的，不是卡住了。

## 已知取舍

- **Kafka 不挂数据卷**：`apache/kafka` 镜像以非 root 运行，新建命名卷是 root 属主，
  挂上去直接启动失败。开发栈丢消息无所谓，topic 由 `kafka-init` 幂等重建。
  要持久化：加 `user: root` 并显式指定 `KAFKA_LOG_DIRS`。
- **StarRocks 用 allin1**：FE + BE 同容器，本地够用；生产请拆。
- **Redis 不在任务书点名的六大组件里**，但 `controlplane/store.py` 确实读它，
  缺了控制面就是半条腿，所以一起起了。
- **宿主机端口独占 186xx 号段**：不是 1:1 发布。容器里各组件仍监听原生端口
  （9000/9001/3306/9030/8030/8040/7474/7687/6379/8081/8083/9092），
  宿主机侧统一发布到 18600–18692，`config.py` / `.env.example` 的默认值与之逐条对齐。
  要改端口就三处一起改（`docker/compose.yaml` 左半边 + `config.py` + `.env.example`），
  改完跑 `make ports` 验；**右半边（容器端口）任何情况下都不要动**。
