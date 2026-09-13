# =============================================================================
# ADAS 闭环湖仓 · Flink 运行镜像
#
# 在官方 flink 镜像上补齐本项目 flink/sql/*.sql 真正用到的 connector：
#   · Paimon      —— 湖格式本体（catalog / 主键表 / changelog）
#   · S3          —— warehouse 落在 MinIO，Paimon 侧与 Flink 侧各要一份
#   · Kafka       —— 通道二（车端触发事件、产线事件）与 lineage.change.event
#   · MySQL CDC   —— 通道一（业务库 binlog 实时入湖，三阶段）
#
# 所有版本写死，不用 latest；每个 jar 都用 Maven Central 的 .sha1 校验，
# 下载被劫持或截断会直接让 build 失败。
# =============================================================================

# -----------------------------------------------------------------------------
# 版本选型说明（为什么是这几个数字）
#
# FLINK_VERSION=1.20.1
#   1.20 是 Flink 1.x 的最后一个特性版本（长期维护线），Paimon、Flink CDC、
#   Kafka connector 三家在这个版本上的适配都是齐的，避免 2.0 的 API 断层。
#
# PAIMON_VERSION=1.0.1
#   仓库里的 SQL（flink/sql/00_catalog_init.sql、ddl/00_catalog.sql）只写了
#   'type' = 'paimon' 与 'metastore' = 'filesystem'，没有钉版本；src/ 里也没有
#   任何 Paimon 版本字面量。按任务要求选 Paimon 1.x 稳定版：1.0.x 是 Paimon
#   进入 1.0 后的第一个稳定补丁线，主键表 / changelog-producer / bucket 这些
#   本项目 catalog/spec.py 里硬校验的物理策略在 1.0 已全部 GA。
#   适配包取 paimon-flink-1.20，与 FLINK_VERSION 的 minor 对齐。
#
# KAFKA_CONNECTOR_VERSION=3.3.0-1.20
#   Flink Kafka connector 独立发版，后缀即目标 Flink minor。
#
# MYSQL_CDC_VERSION=3.3.0
#   Flink CDC 3.3.0 支持 Flink 1.20；3.1 起 groupId 已迁到 org.apache.flink。
#   本项目 CDC 用了 scan.incremental.snapshot.enabled=true（无锁并行快照、
#   业务库零侵入），这是增量快照框架的能力，3.x 才完整。
# -----------------------------------------------------------------------------
ARG FLINK_VERSION=1.20.1

FROM flink:${FLINK_VERSION}-java17

ARG FLINK_VERSION=1.20.1
ARG PAIMON_VERSION=1.0.1
ARG PAIMON_FLINK_MINOR=1.20
ARG KAFKA_CONNECTOR_VERSION=3.3.0-1.20
ARG MYSQL_CDC_VERSION=3.3.0
ARG SHADED_HADOOP_VERSION=2.8.3-10.0
ARG MYSQL_DRIVER_VERSION=8.4.0
ARG MAVEN_REPO=https://repo1.maven.org/maven2

LABEL org.opencontainers.image.title="adas-lakehouse-flink" \
      org.opencontainers.image.description="Flink ${FLINK_VERSION} + Paimon ${PAIMON_VERSION} + Kafka + MySQL CDC" \
      adas.flink.version="${FLINK_VERSION}" \
      adas.paimon.version="${PAIMON_VERSION}" \
      adas.kafka-connector.version="${KAFKA_CONNECTOR_VERSION}" \
      adas.mysql-cdc.version="${MYSQL_CDC_VERSION}" \
      adas.shaded-hadoop.version="${SHADED_HADOOP_VERSION}" \
      adas.mysql-driver.version="${MYSQL_DRIVER_VERSION}"

USER root

# curl：下 jar + healthcheck 用；procps：TaskManager 的 healthcheck 要 pgrep；
# mysql 客户端不装（StarRocks 那边另有 init 容器）。
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl procps; \
    rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# connector jar：下载 + sha1 校验
#
# .sha1 与 jar 同源于 Maven Central，校验的是「下载完整且未被中间篡改」，
# 不是供应链签名；要更强保证请自行改成固定 sha512 白名单。
#
# 四个 jar 分别是：
#   paimon-flink-1.20            Paimon 的 Flink 适配包（catalog / 主键表 /
#                                changelog / compaction 全在这里）
#   paimon-s3                    S3（Paimon 侧）：warehouse=s3://... 靠它
#   flink-sql-connector-kafka    通道二事件流 + lineage.change.event
#   flink-sql-connector-mysql-cdc 通道一：全量快照 → 增量 binlog → 断点续传
#   flink-shaded-hadoop-2-uber   Paimon 的硬依赖：它在建 catalog 时就要
#                                org.apache.hadoop.conf.Configuration，而
#                                paimon-flink / paimon-s3 两个 bundle 都不含
#                                unshaded 的 Hadoop 类（实测 0 个 class）。
#                                缺了它 CREATE CATALOG 直接 ClassNotFoundException。
#                                flink-s3-fs-hadoop 插件里的 Hadoop 是 shade 过的，
#                                而且插件类加载器隔离，顶不了这个位。
#   mysql-connector-j            MySQL JDBC 驱动。Flink CDC 3.x 因为 GPL 许可
#                                已经不再把它打进 fat jar，必须单独放；缺了它
#                                建 mysql-cdc 表就是
#                                ClassNotFoundException: com.mysql.cj.jdbc.Driver。
#                                （GPLv2 + FOSS 例外，分发本镜像前自行确认合规。）
#
# 注意：下面整个 RUN 内部不写 `#` 注释 —— Dockerfile 的注释行会在续行拼接前被
# 剥离，混在 `\` 续行里容易踩坑，说明一律放在指令外面。
# -----------------------------------------------------------------------------
RUN set -eux; \
    fetch() { \
      url="$1"; dest="$2"; \
      echo "==> ${url}"; \
      curl -fsSL --retry 5 --retry-delay 3 -o "${dest}" "${url}"; \
      curl -fsSL --retry 5 --retry-delay 3 -o "${dest}.sha1" "${url}.sha1"; \
      printf '%s  %s\n' "$(cut -c1-40 "${dest}.sha1")" "${dest}" | sha1sum -c -; \
      rm -f "${dest}.sha1"; \
    }; \
    fetch "${MAVEN_REPO}/org/apache/paimon/paimon-flink-${PAIMON_FLINK_MINOR}/${PAIMON_VERSION}/paimon-flink-${PAIMON_FLINK_MINOR}-${PAIMON_VERSION}.jar" \
          "/opt/flink/lib/paimon-flink-${PAIMON_FLINK_MINOR}-${PAIMON_VERSION}.jar"; \
    fetch "${MAVEN_REPO}/org/apache/paimon/paimon-s3/${PAIMON_VERSION}/paimon-s3-${PAIMON_VERSION}.jar" \
          "/opt/flink/lib/paimon-s3-${PAIMON_VERSION}.jar"; \
    fetch "${MAVEN_REPO}/org/apache/flink/flink-sql-connector-kafka/${KAFKA_CONNECTOR_VERSION}/flink-sql-connector-kafka-${KAFKA_CONNECTOR_VERSION}.jar" \
          "/opt/flink/lib/flink-sql-connector-kafka-${KAFKA_CONNECTOR_VERSION}.jar"; \
    fetch "${MAVEN_REPO}/org/apache/flink/flink-sql-connector-mysql-cdc/${MYSQL_CDC_VERSION}/flink-sql-connector-mysql-cdc-${MYSQL_CDC_VERSION}.jar" \
          "/opt/flink/lib/flink-sql-connector-mysql-cdc-${MYSQL_CDC_VERSION}.jar"; \
    fetch "${MAVEN_REPO}/org/apache/flink/flink-shaded-hadoop-2-uber/${SHADED_HADOOP_VERSION}/flink-shaded-hadoop-2-uber-${SHADED_HADOOP_VERSION}.jar" \
          "/opt/flink/lib/flink-shaded-hadoop-2-uber-${SHADED_HADOOP_VERSION}.jar"; \
    fetch "${MAVEN_REPO}/com/mysql/mysql-connector-j/${MYSQL_DRIVER_VERSION}/mysql-connector-j-${MYSQL_DRIVER_VERSION}.jar" \
          "/opt/flink/lib/mysql-connector-j-${MYSQL_DRIVER_VERSION}.jar"; \
    chown -R flink:flink /opt/flink/lib

# -----------------------------------------------------------------------------
# S3（Flink 侧）—— checkpoint/savepoint 想落 s3:// 时要的插件。
# 官方镜像已把 jar 放在 /opt/flink/opt/，按 Flink 的插件机制搬进 plugins/ 即生效，
# 不需要再下一次（版本天然与 FLINK_VERSION 对齐）。
# -----------------------------------------------------------------------------
RUN set -eux; \
    mkdir -p /opt/flink/plugins/s3-fs-hadoop; \
    cp "/opt/flink/opt/flink-s3-fs-hadoop-${FLINK_VERSION}.jar" /opt/flink/plugins/s3-fs-hadoop/; \
    chown -R flink:flink /opt/flink/plugins

# -----------------------------------------------------------------------------
# 基础配置与目录
# checkpoints/savepoints 先在镜像里建好并 chown —— 这样 compose 的命名卷
# 首次创建时会继承 flink:flink 属主，容器以非 root 跑也写得进去。
# -----------------------------------------------------------------------------
COPY conf/config.yaml /tmp/adas-flink-config.yaml
COPY bin/sql-gateway-entrypoint.sh /opt/adas/bin/sql-gateway-entrypoint.sh

RUN set -eux; \
    mkdir -p /opt/flink/checkpoints /opt/flink/savepoints /opt/adas/flink-sql /opt/adas/ddl; \
    target=/opt/flink/conf/config.yaml; \
    [ -f "$target" ] || target=/opt/flink/conf/flink-conf.yaml; \
    printf '\n' >> "$target"; \
    cat /tmp/adas-flink-config.yaml >> "$target"; \
    rm -f /tmp/adas-flink-config.yaml; \
    chmod +x /opt/adas/bin/sql-gateway-entrypoint.sh; \
    chown -R flink:flink /opt/flink/checkpoints /opt/flink/savepoints /opt/flink/conf /opt/adas

USER flink

EXPOSE 6123 8081 8083
