from dataclasses import dataclass


@dataclass
class DatasourceInfo:
    """Phase 3.4：数据源实体（可管理）。

    存储业务数据仓库的连接信息，支持页面 CRUD 管理。
    password 字段在落库前用 Fernet 加密（见 DatasourceInfoMySQL ORM）。

    与运行时配置 app_config.db_dw 的关系：
    - 当前查询执行仍读 app_config.db_dw（兼容既有 graph 链路）。
    - datasource 实体作为"管理面"，为未来动态切换数据源、多租户隔离做准备。
    - admin 可通过 API 维护数据源清单，密码加密存储。
    """

    id: str  # 数据源标识，如 "warehouse_audio"
    name: str  # 展示名称
    dialect: str  # 方言：mysql/postgresql/clickhouse/doris
    host: str
    port: int
    database: str
    user: str
    password: str  # 落库前加密；读取时解密；API 返回时脱敏
    active: bool = False  # 是否为当前激活的数据源（同一时刻建议只有一个 active）
    description: str = ""
