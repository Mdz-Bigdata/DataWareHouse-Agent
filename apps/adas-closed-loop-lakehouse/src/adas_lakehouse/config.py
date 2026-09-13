"""全栈连接配置：Flink / Paimon / StarRocks / Neo4j / MinIO / Kafka。

所有默认值对齐 docker/compose.yaml，`make up` 起来即可直接用。
生产环境一律走环境变量覆盖，不要改这里的默认值。

⚠️ 端口口径：宿主机 186xx 独占号段 vs 容器内原生默认端口
------------------------------------------------------------------
本文件里的每一个端口都是**宿主机视角**——给宿主机上跑的 Python 客户端连的。
它们统一取 186xx 独占号段，为的是避开同一仓库里那套既有平台栈
（仓库根 compose.yaml 已发布 8080/8000/3000/8020/8030/8040/6379/6333/9200），
本项目让路，不与之抢端口。对照表（宿主机发布端口 ← 容器内端口）::

    MinIO S3              18600 ← 9000      MinIO Console       18601 ← 9001
    MySQL(CDC 源)         18606 ← 3306      StarRocks 查询      18630 ← 9030
    StarRocks FE HTTP     18631 ← 8030      StarRocks BE HTTP   18640 ← 8040
    Neo4j Browser         18674 ← 7474      Neo4j Bolt          18687 ← 7687
    Redis(控制面)         18679 ← 6379      Flink Web UI        18681 ← 8081
    Flink SQL Gateway     18683 ← 8083      Kafka               18692 ← 9092

（StarRocks FE HTTP 落在 18631 而不是 18630，是因为 18630 已经给查询端口占了。）

**容器内端口一律没变**，仍是各组件的原生默认值（右列）。因此：在 compose 网络
内部运行时（Flink / StarRocks 容器里的作业、容器内执行的 SQL），不要用这里的默认
值，要用环境变量覆盖成「服务名 + 容器端口」，例如::

    MINIO_ENDPOINT=http://minio:9000
    KAFKA_BOOTSTRAP_SERVERS=kafka:29092
    STARROCKS_FE_HOST=starrocks   STARROCKS_QUERY_PORT=9030  STARROCKS_HTTP_PORT=8030
    NEO4J_URI=bolt://neo4j:7687
    FLINK_JOBMANAGER_URL=http://jobmanager:8081
    FLINK_SQL_GATEWAY_URL=http://sql-gateway:8083

容器内的 localhost 是容器自己，186xx 在那里一个都不通。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw else default


@dataclass(frozen=True, slots=True)
class MinioConfig:
    """对象存储：Paimon warehouse 与合规入湖的原始文件都落这里。"""

    endpoint: str = field(default_factory=lambda: _env("MINIO_ENDPOINT", "http://localhost:18600"))
    access_key: str = field(default_factory=lambda: _env("MINIO_ACCESS_KEY", "adas"))
    secret_key: str = field(default_factory=lambda: _env("MINIO_SECRET_KEY", "adas-secret"))
    warehouse_bucket: str = field(
        default_factory=lambda: _env("PAIMON_WAREHOUSE_BUCKET", "adas-lakehouse")
    )
    raw_bucket: str = field(default_factory=lambda: _env("RAW_BUCKET", "adas-raw"))

    @property
    def warehouse_path(self) -> str:
        return f"s3://{self.warehouse_bucket}/warehouse"


@dataclass(frozen=True, slots=True)
class PaimonConfig:
    catalog: str = field(default_factory=lambda: _env("PAIMON_CATALOG", "paimon"))
    database: str = field(default_factory=lambda: _env("PAIMON_DATABASE", "adas_lakehouse"))
    metastore: str = field(default_factory=lambda: _env("PAIMON_METASTORE", "filesystem"))


@dataclass(frozen=True, slots=True)
class FlinkConfig:
    jobmanager_url: str = field(
        default_factory=lambda: _env("FLINK_JOBMANAGER_URL", "http://localhost:18681")
    )
    sql_gateway_url: str = field(
        default_factory=lambda: _env("FLINK_SQL_GATEWAY_URL", "http://localhost:18683")
    )
    parallelism: int = field(default_factory=lambda: _env_int("FLINK_PARALLELISM", 2))
    checkpoint_interval_ms: int = field(
        default_factory=lambda: _env_int("FLINK_CHECKPOINT_INTERVAL_MS", 60_000)
    )


@dataclass(frozen=True, slots=True)
class StarRocksConfig:
    """双路查询：External Catalog 直查 Paimon + 内表物化做毫秒级直查与向量检索。"""

    fe_host: str = field(default_factory=lambda: _env("STARROCKS_FE_HOST", "localhost"))
    #: 宿主机发布端口（容器内仍是 9030）
    query_port: int = field(default_factory=lambda: _env_int("STARROCKS_QUERY_PORT", 18630))
    #: 宿主机发布端口（容器内仍是 8030；18630 已被查询端口占用故取 18631）
    http_port: int = field(default_factory=lambda: _env_int("STARROCKS_HTTP_PORT", 18631))
    user: str = field(default_factory=lambda: _env("STARROCKS_USER", "root"))
    password: str = field(default_factory=lambda: _env("STARROCKS_PASSWORD", ""))
    external_catalog: str = field(
        default_factory=lambda: _env("STARROCKS_EXTERNAL_CATALOG", "paimon_catalog")
    )
    internal_database: str = field(default_factory=lambda: _env("STARROCKS_DATABASE", "adas_ads"))


@dataclass(frozen=True, slots=True)
class Neo4jConfig:
    """血缘关系视图。口诀：图库找关系、湖仓取明细。"""

    uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://localhost:18687"))
    user: str = field(default_factory=lambda: _env("NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", "adas-lineage"))
    database: str = field(default_factory=lambda: _env("NEO4J_DATABASE", "neo4j"))
    #: 多跳遍历建议限定 3-5 跳防止扇出爆炸
    max_traversal_depth: int = field(default_factory=lambda: _env_int("NEO4J_MAX_DEPTH", 5))


@dataclass(frozen=True, slots=True)
class KafkaConfig:
    """三通道入湖里的实时通道：车端回传事件、产线事件。"""

    bootstrap_servers: str = field(
        default_factory=lambda: _env("KAFKA_BOOTSTRAP_SERVERS", "localhost:18692")
    )
    group_id: str = field(default_factory=lambda: _env("KAFKA_GROUP_ID", "adas-lakehouse"))
    trigger_topic: str = field(
        default_factory=lambda: _env("KAFKA_TRIGGER_TOPIC", "vehicle.trigger.event")
    )
    production_topic: str = field(
        default_factory=lambda: _env("KAFKA_PRODUCTION_TOPIC", "production.event")
    )


@dataclass(frozen=True, slots=True)
class Settings:
    minio: MinioConfig = field(default_factory=MinioConfig)
    paimon: PaimonConfig = field(default_factory=PaimonConfig)
    flink: FlinkConfig = field(default_factory=FlinkConfig)
    starrocks: StarRocksConfig = field(default_factory=StarRocksConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    kafka: KafkaConfig = field(default_factory=KafkaConfig)


@lru_cache(maxsize=1)
def settings() -> Settings:
    """进程级单例。测试里改环境变量后调 settings.cache_clear() 重新读取。"""
    return Settings()
