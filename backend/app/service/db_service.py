# -*- coding: utf-8 -*-
import os
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sqlglot
from sqlalchemy import create_engine
from dotenv import load_dotenv

# 自动加载当前目录或上层目录中的 .env 配置文件
load_dotenv()

# NOTE: 本模块提供基于内存 SQLite 的 Doris/ClickHouse/PostgreSQL 仿真数据库服务。
# 它能将传入的不同数据库方言通过 SQLGlot 转译为 SQLite 方言并执行，同时内置了电商交易仿真数据。

class DBService:
    def __init__(self):
        # 初始化内存 SQLite 连接
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._register_sqlite_udfs()
        self._initialize_mock_data()
        
        # 动态检测并构建真实物理数据库连接池
        self.real_engine = None
        self.active_db_type = "sqlite"
        self._setup_real_database_connection()

    def _register_sqlite_udfs(self):
        """
        注册一些 ClickHouse / Doris 中常用的函数到 SQLite，以防止转译后的边缘 case 执行报错。
        """
        # 模拟 toStartOfMonth/date_trunc 等
        def to_start_of_month(date_str):
            try:
                dt = pd.to_datetime(date_str)
                return dt.strftime("%Y-%m-01")
            except:
                return date_str

        def date_sub(*args):
            if not args:
                return ""
            date_str = args[0]
            days = 0
            for val in args[1:]:
                try:
                    days = int(val)
                    break
                except:
                    if isinstance(val, str):
                        import re
                        num = re.findall(r"\d+", val)
                        if num:
                            days = int(num[0])
                            break
            try:
                dt = pd.to_datetime(date_str)
                return (dt - timedelta(days=days)).strftime("%Y-%m-%d")
            except:
                return date_str

        self.conn.create_function("toStartOfMonth", 1, to_start_of_month)
        self.conn.create_function("to_start_of_month", 1, to_start_of_month)
        self.conn.create_function("date_trunc", 2, lambda unit, dt: to_start_of_month(dt) if 'month' in unit.lower() else dt)
        self.conn.create_function("date_sub", -1, date_sub)
        self.conn.create_function("toIntervalDay", 1, lambda days: int(days))

    def _initialize_mock_data(self):
        """
        根据用户最新硬性指示：彻底移除所有仿真数据！
        本方法不再灌装任何 mock 电商或文章种子数据集，保证物理数据源为唯一数据源。
        """
        pass

    def _setup_real_database_connection(self):
        """
        动态加载物理数据库连接。
        优先从系统环境变量中提取连接参数（支持 .env 方式），其次回退读取 backend/llm_config.json。
        """
        # 1. 优先尝试从环境变量读取 (.env 方式)，兼容 DATABASE_URL 与 DB_URL
        db_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
        db_type = os.getenv("DB_TYPE")
        pool_size_env = os.getenv("DB_POOL_SIZE")
        max_overflow_env = os.getenv("DB_MAX_OVERFLOW")
        
        # 自动将异步连接协议重写为同步连接协议，以支持 pandas 和 SQLAlchemy 同步连接池
        if db_url:
            if "postgresql+asyncpg://" in db_url:
                db_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
            elif "mysql+aiomysql://" in db_url:
                db_url = db_url.replace("mysql+aiomysql://", "mysql+pymysql://")
        
        pool_size = int(pool_size_env) if pool_size_env else 10
        max_overflow = int(max_overflow_env) if max_overflow_env else 20
        
        # 2. 如果环境变量没有提供 DB_URL / DATABASE_URL，则回退读取 llm_config.json
        if not db_url:
            config_path = "/Users/mindezhi/DataWareHouse-Agent/backend/llm_config.json"
            if os.path.exists(config_path):
                try:
                    import json
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                    db_cfg = config_data.get("database")
                    if db_cfg:
                        active_db = db_cfg.get("active_db", "sqlite")
                        if active_db != "sqlite":
                            conn_info = db_cfg.get("connections", {}).get(active_db, {})
                            db_url = conn_info.get("url")
                            db_type = active_db
                            pool_size = conn_info.get("pool_size", pool_size)
                            max_overflow = conn_info.get("max_overflow", max_overflow)
                except Exception as e:
                    print(f"[DBService] 读取 llm_config.json 错误: {e}")
                    
        # 3. 如果成功获取连接串，初始化连接池
        if db_url:
            try:
                # 自动从连接串协议中嗅探数据库类型，支持 clickhouse, postgres, mysql/doris/starrocks
                if not db_type:
                    db_url_lower = db_url.lower()
                    if "postgres" in db_url_lower:
                        db_type = "postgres"
                    elif "clickhouse" in db_url_lower:
                        db_type = "clickhouse"
                    elif "mysql" in db_url_lower:
                        db_type = "mysql"
                    else:
                        db_type = "mysql"
                        
                self.real_engine = create_engine(
                    db_url,
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                    pool_pre_ping=True,
                    connect_args={"connect_timeout": 2}
                )
                self.active_db_type = db_type
                print(f"[DBService] 真实物理数据源加载成功！类型: {self.active_db_type.upper()}, 连接地址: {db_url.split('@')[-1]}")
            except Exception as e:
                print(f"[DBService] 初始化数据库 Engine 失败: {e}。")

    def execute_query(self, sql: str, dialect: str = "mysql") -> pd.DataFrame:
        """
        接收指定方言的 SQL，将其转译并直接在物理数据库上执行。
        如果物理执行失败，则直接抛出异常，绝不进行仿真降级。
        """
        sql = sql.strip().rstrip(";")
        
        # 1. 物理数据库查询通道
        if self.real_engine:
            try:
                db_type_lower = self.active_db_type.lower()
                if "postgre" in db_type_lower:
                    target_dialect = "postgres"
                elif "clickhouse" in db_type_lower:
                    target_dialect = "clickhouse"
                elif "doris" in db_type_lower:
                    target_dialect = "doris"
                elif "starrocks" in db_type_lower:
                    target_dialect = "starrocks"
                else:
                    target_dialect = "mysql"
                    
                translated_sqls = sqlglot.transpile(sql, read=dialect, write=target_dialect)
                target_sql = translated_sqls[0]
                
                # PostgreSQL 不支持 database.table 语法，去掉 db_name 前缀
                if "postgres" in target_dialect:
                    db_name = self.get_active_db_name()
                    target_sql = target_sql.replace(f"{db_name}.", "")
                
                print(f"Executing query on {self.active_db_type.upper()}:\n--- Source ({dialect}) ---\n{sql}\n--- Target ({self.active_db_type}) ---\n{target_sql}")
                
                with self.real_engine.connect() as connection:
                    df = pd.read_sql_query(target_sql, connection)
                return df
            except Exception as e:
                print(f"[DBService] 真实物理数据库执行失败: {e}。抛出错误，拒绝 Fallback 到仿真通道！")
                raise e
        else:
            # 当物理库未连接时，走本地内存 SQLite 执行（用于单测）
            try:
                translated_sqls = sqlglot.transpile(sql, read=dialect, write="sqlite")
                sqlite_sql = translated_sqls[0]
                db_name = self.get_active_db_name()
                sqlite_sql = sqlite_sql.replace(f"{db_name}.", "")
                import re
                sqlite_sql = re.sub(r"TIMESTAMP_TRUNC\(([^,]+),\s*MONTH\)", r"strftime('%Y-%m-01', \1)", sqlite_sql, flags=re.IGNORECASE)
                sqlite_sql = re.sub(r"date_trunc\('month',\s*([^)]+)\)", r"strftime('%Y-%m-01', \1)", sqlite_sql, flags=re.IGNORECASE)
            except Exception as e:
                sqlite_sql = sql
                db_name = self.get_active_db_name()
                sqlite_sql = sqlite_sql.replace(f"{db_name}.", "")
                
            print(f"Executing query on SQLite:\n--- Source ({dialect}) ---\n{sql}\n--- Target (sqlite) ---\n{sqlite_sql}")
            df = pd.read_sql_query(sqlite_sql, self.conn)
            return df

    def get_active_db_name(self) -> str:
        """
        动态从当前物理连接串中解析当前的数据库名称。
        - 优先从真实的 db_url 中提取最后一级路径作为数据库名。
        - 若无法解析或使用 SQLite，则默认返回 'blog_converter' 兜底。
        """
        db_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
        if not db_url:
            # 回退读取 llm_config.json
            config_path = "/Users/mindezhi/DataWareHouse-Agent/backend/llm_config.json"
            if os.path.exists(config_path):
                try:
                    import json
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                    db_cfg = config_data.get("database")
                    if db_cfg:
                        active_db = db_cfg.get("active_db", "sqlite")
                        if active_db != "sqlite":
                            conn_info = db_cfg.get("connections", {}).get(active_db, {})
                            db_url = conn_info.get("url")
                except:
                    pass
        if db_url:
            try:
                # 解析 db_url 里的物理数据库名字。如 postgresql+psycopg2://localhost:5432/blog_converter?sslmode=disable
                # 先剥离 url query 参数
                main_part = db_url.split("?")[0]
                db_name = main_part.split("/")[-1]
                if db_name:
                    return db_name
            except:
                pass
        return "blog_converter"

    def get_table_schema(self, table_name: str) -> str:
        """
        获取仿真表结构的 DDL 定义
        """
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        res = cursor.fetchone()
        return res[0] if res else ""

# 单例实例，方便跨 API 和服务共享
db_service = DBService()
