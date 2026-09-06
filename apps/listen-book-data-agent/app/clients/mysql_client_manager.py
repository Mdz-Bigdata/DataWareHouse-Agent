import asyncio

from sqlalchemy import URL, Select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.conf.app_config import DBConfig, app_config
from app.models.mysql.table_info_mysql import TableInfoMySQL
from app.repositories.dialect import get_dialect_strategy

# engine = create_async_engine(
#     "mysql+asyncmy://user:pass@hostname/dbname?charset=utf8mb4"
# )


class MysqlCientManager:
    """数据库连接管理器（Phase 3.3 支持多方言）。

    dialect 参数决定 SQLAlchemy 异步驱动：
    - mysql/doris: mysql+asyncmy（Doris 兼容 MySQL 协议）
    - postgresql: postgresql+asyncpg
    - clickhouse: clickhouse+asynch
    默认 mysql（兼容既有调用方）。
    """

    def __init__(self, config: DBConfig, dialect: str = "mysql"):
        self.config = config
        self.dialect_name = dialect
        self.client: AsyncEngine | None = None
        self.session_factory: async_sessionmaker | None = None

    # 返回连接的url（按方言选择 drivername）
    def _get_url(self):
        strategy = get_dialect_strategy(self.dialect_name)
        # MySQL/Doris 需要指定 charset；其他方言按各自驱动默认
        query = {"charset": "utf8mb4"} if self.dialect_name in ("mysql", "doris") else {}
        return URL.create(
            drivername=strategy.drivername,
            username=self.config.user,
            password=self.config.password,
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
            query=query,
        )

    def init_client(self):
        self.client = create_async_engine(
            self._get_url(),
            pool_size=10,
            max_overflow=5,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 10,
                "read_timeout": 60,
            },
        )
        # 创建session工厂
        # expire_on_commit=False：async 场景下 commit 后属性过期再访问会触发
        # greenlet 外的隐式 IO（MissingGreenlet），提交后对象保持已加载状态。
        self.session_factory = async_sessionmaker(
            self.client,
            autoflush=False,
            autobegin=True,
            autocommit=False,
            expire_on_commit=False,
        )

    async def close(self):
        await self.client.dispose()


# 创建用于操作dw和meta库的manager实例
# Phase 3.3：dw 库按配置的 dialect 选择驱动；meta 库始终 mysql（存语义层元数据）
dw_mysql_client_manager = MysqlCientManager(app_config.db_dw, dialect=app_config.db_dw.dialect)
meta_mysql_client_manager = MysqlCientManager(app_config.db_meta, dialect="mysql")


if __name__ == "__main__":

    async def test():
        # 初始化客户端
        dw_mysql_client_manager.init_client()
        # 创建异步会话
        # assert dw_mysql_client_manager.session_factory is not None
        async with dw_mysql_client_manager.session_factory() as session:
            """
            result.all(): [row对象，row对象] row对象包含了当前行的数据，可以遍历它们
            result.mappings().all(): [rowMapping对象，rowMapping对象] 对象包含了当前行的字段名和数据，可以遍历它们
            result.scalars().all(): [val1, value2]  第一列字段值的列表
            """
            session: AsyncSession  # 声明session类型

            # 执行查询SQL
            sql = "select * from dim_customer limit 2"
            result = await session.execute(text(sql))
            # 读取所有结果数据
            # rows = result.all()
            # print(rows, type(rows[0]))
            # print(rows[0].customer_name)
            # for row in rows:
            #     for val in row:
            #         print(val)

            # rows = result.mappings().all()
            # print(rows, type(rows[0]))
            # print(rows[0].customer_name, rows[0]["customer_name"])
            # for row in rows:
            #     for key,val in row.items():
            #         print(key, val)

            rows = result.scalars().all()
            print(rows, type(rows[0]))
            for val in rows:
                print(val)

    # 插入和查询
    async def test_ORM():
        meta_mysql_client_manager.init_client()

        assert meta_mysql_client_manager.session_factory is not None
        async with meta_mysql_client_manager.session_factory() as session:
            # 插入数据
            table_info = TableInfoMySQL(
                id="dim_customer", name="dim_customer", role="dim", description="客户信息表"
            )
            session.add(table_info)
            table_info2 = TableInfoMySQL(
                id="dim_customer2", name="dim_customer2", role="dim", description="客户信息表"
            )
            table_info3 = TableInfoMySQL(
                id="dim_customer3", name="dim_customer3", role="dim", description="客户信息表"
            )
            session.add_all([table_info2, table_info3])

            # 提交事务
            # await session.commit()

            # 查询数据 单个
            table_info_a = await session.get(TableInfoMySQL, "dim_customer")
            print(table_info_a, table_info_a.name)

            # 查询数据 多个
            result = await session.execute(Select(TableInfoMySQL).limit(2))
            # [table_info对象， table_info对象]
            table_infos: list[TableInfoMySQL] = result.scalars().all()

            for item in table_infos:
                print(item, item.description)

        await meta_mysql_client_manager.close()

    # 更新和删除
    async def test_ORM2():
        meta_mysql_client_manager.init_client()

        assert meta_mysql_client_manager.session_factory is not None
        async with meta_mysql_client_manager.session_factory() as session:
            table_info = await session.get(TableInfoMySQL, "dim_customer")

            # 更新数据
            # table_info.description = "xxxx"
            # 删除数据
            await session.delete(table_info)
            # 提交事务
            await session.commit()

        await meta_mysql_client_manager.close()

    # 执行异步函数
    asyncio.run(test())
