import os
from dataclasses import dataclass
from pathlib import Path

from omegaconf import OmegaConf


# ==================== logging配置模型 ====================
@dataclass
class ApplicationConfig:
    name: str
    environment: str


@dataclass
class Console:
    enable: bool
    level: str


@dataclass
class File:
    enable: bool
    level: str
    path: str
    rotation: str
    retention: str


@dataclass
class LoggingConfig:
    file: File
    console: Console


# ==================== database配置模型 ====================


@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    # Phase 3.3：数据源方言，默认 mysql。支持 mysql/postgresql/clickhouse/doris。
    # 仅 db_dw 生效（db_meta 始终用 mysql 存语义层元数据）。
    dialect: str = "mysql"


# ==================== Qdrant 配置模型 ====================


@dataclass
class QdrantConfig:
    host: str
    port: int
    embedding_size: int
    collection_prefix: str


# ==================== Embedding 配置模型 ====================


@dataclass
class EmbeddingConfig:
    host: str
    port: int
    model: str


# ==================== ES 配置模型 ====================


@dataclass
class ESConfig:
    host: str
    port: int
    index_name: str


# ==================== Redis 配置模型（Phase 4.1） ====================


@dataclass
class RedisConfig:
    host: str
    port: int
    db: int
    password: str
    # 元数据缓存 TTL（秒），默认 10 分钟
    meta_ttl_seconds: int
    # 查询结果缓存 TTL（秒），默认 5 分钟
    query_ttl_seconds: int


# ==================== OpenTelemetry 配置模型（Phase 4.3） ====================


@dataclass
class OTelConfig:
    # 是否启用 OTel tracing。生产建议开启，开发环境可关闭减少噪声。
    enable: bool
    # OTLP 导出端点（HTTP 协议），如 http://otel-collector:4318/v1/traces。
    # 为空时用 ConsoleSpanExporter（本地调试）。
    otlp_endpoint: str
    # 服务名（在 Jaeger/Tempo 里显示的应用名）
    service_name: str


# ==================== LLM 配置模型 ====================


@dataclass
class LLMConfig:
    provider: str
    model_name: str
    api_key: str
    base_url: str
    temperature: float
    timeout_seconds: int
    key_master_secret: str


@dataclass
class QueryConfig:
    timeout_seconds: int
    max_result_rows: int
    correction_attempts: int
    explain_cost_budget: float = 5_000_000
    explain_rows_budget: int = 5_000_000
    # legacy：现有 LLM/确定性 SQL 链路；dsl：优先 JSON DSL 编译，失败自动回退。
    generation_mode: str = "legacy"


# ==================== 认证配置模型 ====================


@dataclass
class AuthConfig:
    secret_key: str
    token_ttl_minutes: int
    admin_username: str
    admin_password: str


# ==================== CORS 配置模型（Phase 0.4） ====================


@dataclass
class CORSConfig:
    # 是否启用 CORS。生产同域部署可关闭；开发态前后端分离需要开启。
    enable: bool
    # 允许的来源，逗号分隔字符串（避开 OmegaConf 嵌套 resolver 的逗号歧义）。
    # 形如 "http://localhost:5173,http://localhost:8080"；"*" 表示放开（不推荐生产）。
    allowed_origins: str
    allowed_methods: list[str]
    allowed_headers: list[str]
    allow_credentials: bool


# ==================== 限流配置模型（Phase 0.5） ====================


@dataclass
class RateLimitConfig:
    # 是否启用限流。
    enable: bool
    # 登录防爆破：按 IP 限流。
    login: str
    # 查询端点（含 LLM 成本）：按用户限流。
    query: str


# ==================== 应用总配置模型 ====================


@dataclass
class AppConfig:
    app: ApplicationConfig
    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig
    query: QueryConfig
    auth: AuthConfig
    cors: CORSConfig
    rate_limit: RateLimitConfig
    redis: RedisConfig
    otel: OTelConfig


# 配置文件路径
_default_yaml_path = Path(__file__).parents[2] / "conf" / "app_config.yaml"
_configured_path = os.getenv("APP_CONFIG_PATH")
_yaml_path = Path(_configured_path).expanduser() if _configured_path else _default_yaml_path

_yaml_data = OmegaConf.load(_yaml_path)

if not _configured_path:
    _local_path = _default_yaml_path.with_name("app_config.local.yaml")
    if _local_path.exists():
        _yaml_data = OmegaConf.merge(_yaml_data, OmegaConf.load(_local_path))

_config = OmegaConf.merge(OmegaConf.structured(AppConfig), _yaml_data)
OmegaConf.resolve(_config)
app_config: AppConfig = OmegaConf.to_object(_config)

if __name__ == "__main__":
    print(app_config.app.name)
    print(app_config.logging.console.level)
