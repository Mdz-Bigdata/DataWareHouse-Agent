# 宿主机端口规划：186xx 号段

本文件是本应用**宿主机发布端口**的唯一权威口径。改动 `docker/compose.yaml`、
`.env.example` 或任何访问地址前，先读这里。

## 0. 结论

本应用的本地依赖栈**独占宿主机 186xx 号段**，与同仓平台
（`./platform.sh up-full`）以及本机其它数据库栈完全不重叠，**可以同时运行**，
不需要任何手工改端口。

---

## 1. 端口对照表

| 服务 | 容器端口 | 宿主机端口 | 用途 | 迁移前 |
|---|---:|---:|---|---:|
| MinIO S3 API | 9000 | **18600** | S3 协议，Paimon 仓库根 `MINIO_ENDPOINT` | 9000 |
| MinIO Console | 9001 | **18601** | 浏览器管理台（`config.py` 不读，纯人用） | 9001 |
| MySQL | 3306 | **18606** | CDC 源库 + 挖掘平台控制面库 | 3306 ★ |
| StarRocks 查询 | 9030 | **18630** | MySQL 协议查询口 `STARROCKS_QUERY_PORT` | 9030 |
| StarRocks FE HTTP | 8030 | **18631** | FE Web UI / REST `STARROCKS_HTTP_PORT` | 8030 ★ |
| StarRocks BE HTTP | 8040 | **18640** | Stream Load 入口（`config.py` 不读） | 8040 ★ |
| Neo4j Browser | 7474 | **18674** | 血缘图库 Web UI | 7474 |
| Neo4j Bolt | 7687 | **18687** | Bolt 协议 `NEO4J_URI` | 7687 |
| Redis | 6379 | **18679** | 控制面状态 `CONTROL_PLANE_REDIS_PORT` | 6379 ★ |
| Flink Web UI | 8081 | **18681** | JobManager REST / Web UI | 8081 |
| Flink SQL Gateway | 8083 | **18683** | SQL Gateway REST | 8083 |
| Kafka | 9092 | **18692** | 宿主机侧 bootstrap（容器网络内走 `kafka:29092`） | 9092 |

★ = 迁移前存在实际冲突。

**编号规则**：`186` + 容器端口末两位。`9000→18600`、`7687→18687`、`9092→18692`
……唯一的例外是 StarRocks FE HTTP：`8030` 的末两位 `30` 已经被
`9030→18630` 占掉，所以顺延一位取 **18631**。

常用访问地址（宿主机浏览器）：

| 地址 | 是什么 |
|---|---|
| <http://localhost:18681> | Flink Web UI（作业、Checkpoint、TaskManager） |
| <http://localhost:18683/v1/info> | Flink SQL Gateway 健康检查 |
| <http://localhost:18601> | MinIO Console（桶与对象） |
| <http://localhost:18674> | Neo4j Browser（血缘图） |
| <http://localhost:18631> | StarRocks FE HTTP |
| `mysql -h 127.0.0.1 -P 18630 -u root` | StarRocks 查询口 |
| `mysql -h 127.0.0.1 -P 18606 -u adas -p` | CDC 源库 / 控制面库 |
| `redis-cli -p 18679` | 控制面状态 |

---

## 2. 为什么是 186xx

### 2.1 宿主机上已经被占掉的端口

**同仓平台**（`/Users/.../DataWareHouse-Agent/compose.yaml`，`./platform.sh up-full`）
发布这些端口，它是既有服务，**不动它，新项目让路**：

| 端口 | 平台组件 |
|---:|---|
| 8080 | `platform-gateway` |
| 8000 | `core-backend` |
| 3000 | `core-web` |
| 8020 | `data-api`（NanZi API Data Platform） |
| 8030 | `agents`（NanZi AI Agent Platform） |
| 8040 | `audio-web`（Listen Book Data Agent） |
| 6379 | `redis` |
| 6333 | `qdrant` |
| 9200 | `elasticsearch` |

**本机另有的数据库栈**（与本仓库无关，但同样占着端口）：

| 端口 | 容器 |
|---:|---|
| 28030 / 29030 | `ossie-doris-fe` |
| 18030 / 19030 | `ossie-starrocks` |

### 2.2 四处真实冲突

迁移前本栈与上面撞车的有四处：

- **8030** — 本栈 StarRocks FE HTTP vs 平台 `agents`；
- **8040** — 本栈 StarRocks BE HTTP vs 平台 `audio-web`；
- **6379** — 本栈 Redis vs 平台 `redis`；
- **3306** — 本栈 MySQL vs 宿主机上已有的 3306 监听者。

结果就是「起了平台就起不了本栈」。

### 2.3 为什么整段搬而不是只挪冲突的四个

只挪冲突端口会留下一张**半新半旧、需要逐条记忆**的映射表，下次平台再加一个服务
又得重新谈判。整段迁移到一个连续、空闲、且有构造规则（`186` + 末两位）的号段，
代价一次性付清，此后**看到 186xx 就知道是本项目**。

选 186xx 的理由：

- **全段空闲**：18600–18699 实测无监听者，与 8xxx / 30xx / 6xxx / 9xxx
  这些拥挤号段彻底错开；
- **不与 `ossie-starrocks` 的 18030 / 19030 相邻误读**——那两个是 `180xx` / `190xx`，
  本项目是 `186xx`，第三位就能区分；
- 落在 IANA 动态/私有端口区间（49152 以下但远离常用服务端口），
  日常开发不会被别的工具随手占用。

自行复核号段是否仍空闲：

```bash
lsof -nP -iTCP -sTCP:LISTEN | awk '{print $9}' | grep -E ':186[0-9]{2}$' | sort -u
# 无输出 = 全段空闲
```

---

## 3. 铁律：容器端口不变，只改宿主机发布端口

这是整个迁移唯一会改坏栈的地方，**务必逐条判断**。

### 3.1 compose `ports:` 只改左半边

```yaml
ports:
  - "18630:9030"    # ✅ 左（宿主机）改，右（容器）不动
  - "18630:18630"   # ❌ 错：容器里 StarRocks 仍然监听 9030，映射到 18630 是空的
```

容器里的进程监听什么端口，由镜像和组件自己的配置决定，**和宿主机发布端口无关**。
把右半边一起改掉 = 把宿主机端口映射到一个没人监听的容器端口，服务表面起来了、
healthcheck 挂掉或连接被拒。

### 3.2 compose 网络内互访：服务名 + 容器端口，一律不动

Flink 访问 MinIO / Kafka / MySQL，StarRocks 建 External Catalog 访问 MinIO，
init 容器访问 Neo4j——全部走 compose 网络，用**服务名 + 容器端口**：

| 容器内口径 | 改不改 |
|---|---|
| `s3.endpoint: http://minio:9000` | **不改** |
| `KAFKA_BOOTSTRAP_SERVERS: kafka:29092` | **不改** |
| `CDC_MYSQL_HOSTNAME: mysql` / `CDC_MYSQL_PORT: "3306"` | **不改** |
| `cypher-shell -a bolt://neo4j:7687` | **不改** |
| `jobmanager.rpc.address: jobmanager` / `rest.port: 8081` | **不改** |
| healthcheck 里的 `http://localhost:8083/v1/info` | **不改**（容器内的 localhost 是容器自己） |

### 3.3 容器内配置里出现的「宿主机地址」要改

少数配置项的作用是**告诉客户端该怎么回连**，它们写的是宿主机口径，必须跟着改：

| 配置项 | 改不改 | 为什么 |
|---|---|---|
| Kafka advertised listener 中面向宿主机的那一条 | **改** → `localhost:18692` | 宿主机客户端按它回连 |
| Kafka advertised listener 中面向容器网络的那一条（`kafka:29092`） | **不改** | 容器网络内回连 |
| `NEO4J_server_bolt_advertised__address` | **改** → `localhost:18687` | 驱动按它重定向 |
| `NEO4J_server_bolt_listen__address: 0.0.0.0:7687` | **不改** | 这是容器内的监听口 |

判断口诀：**这条配置是给「容器网络里的谁」看的，还是给「宿主机上的谁」看的？**
给宿主机看的才改。

### 3.4 宿主机侧客户端（`.env` / `config.py` 默认值）要改

`config.py` 的默认值面向**宿主机上直接跑的 Python**，因此跟宿主机发布端口走：

```bash
MINIO_ENDPOINT=http://localhost:18600
FLINK_JOBMANAGER_URL=http://localhost:18681
FLINK_SQL_GATEWAY_URL=http://localhost:18683
STARROCKS_FE_HOST=localhost
STARROCKS_QUERY_PORT=18630
STARROCKS_HTTP_PORT=18631
NEO4J_URI=bolt://localhost:18687
KAFKA_BOOTSTRAP_SERVERS=localhost:18692
CDC_MYSQL_HOSTNAME=localhost
CDC_MYSQL_PORT=18606
CONTROL_PLANE_MYSQL_HOST=localhost
CONTROL_PLANE_MYSQL_PORT=18606
CONTROL_PLANE_REDIS_HOST=localhost
CONTROL_PLANE_REDIS_PORT=18679
```

对应地，`ddl/*.sql` 与 `flink/sql/*.sql` 里由 `scripts/export_ddl.py` 渲染进去的
endpoint 也是宿主机口径。要在**容器网络内**执行这些 SQL，仍按
[`docker/README.md`](../docker/README.md) 的说明用容器口径重新导出：

```bash
MINIO_ENDPOINT=http://minio:9000 \
KAFKA_BOOTSTRAP_SERVERS=kafka:29092 \
CDC_MYSQL_HOSTNAME=mysql \
CDC_MYSQL_PORT=3306 \
python3 scripts/export_ddl.py
```

注意这段**用的全是容器端口**，端口迁移对它没有任何影响——这正是「只改宿主机发布
端口」的收益：容器网络内的一切保持原样。

---

## 4. 与平台同时运行

```bash
# 终端 A：平台（8080 / 8000 / 3000 / 8020 / 8030 / 8040 / 6379 / 6333 / 9200）
cd /path/to/DataWareHouse-Agent && ./platform.sh up-full

# 终端 B：本应用依赖栈（186xx）
cd /path/to/DataWareHouse-Agent/apps/adas-closed-loop-lakehouse && make up
```

两边端口集合不相交，无需先停任何一边，也**不需要覆盖 `STARROCKS_HTTP_PORT`
或手工改 `docker/compose.yaml`**——那份老的「先改端口再起栈」的说明已经作废。
