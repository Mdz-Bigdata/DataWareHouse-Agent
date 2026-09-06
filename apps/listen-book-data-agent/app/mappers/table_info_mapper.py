from dataclasses import asdict

from app.entities.table_info import TableInfo
from app.models.mysql.table_info_mysql import TableInfoMySQL


class TableInfoMapper:
    @staticmethod
    def to_entity(table_info_mysql: TableInfoMySQL) -> TableInfo:
        """将持久层ORM模型转为业务层模型 用于查询"""
        return TableInfo(
            id=table_info_mysql.id,
            name=table_info_mysql.name,
            role=table_info_mysql.role,
            description=table_info_mysql.description,
            domain=table_info_mysql.domain,
            alias=table_info_mysql.alias or [],
            build_id=table_info_mysql.build_id,
        )

    @staticmethod
    def to_model(table_info: TableInfo) -> TableInfoMySQL:
        """用于操作 将业务层模型转为持久层ORM模型"""
        return TableInfoMySQL(
          **asdict(table_info)
        )
