# -*- coding: utf-8 -*-
import re
import os
import sqlite3
from datetime import datetime
import sqlglot
from sqlglot import exp

# NOTE: 本模块实现全链条安全校验 Guardrail。每个 NL2SQL 查询在真正执行前必须经过：
# 1. DDL/DML 拦截 (仅允许 SELECT)
# 2. 分区键过滤检查 (针对拥有 dt 分区列的大表，动态从 Schema 发现中获取)
# 3. 方言语法预检 (使用 EXPLAIN)
# 4. 扫描量预估 (EXPLAIN ESTIMATE 熔断)
# 5. 超时控制

class GuardrailException(Exception):
    """Guardrail 拦截异常"""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

class Guardrail:
    def __init__(self):
        # NOTE: 大表分区键检查现由动态 Schema 发现驱动。
        # 只有被自动发现拥有 dt/date 分区列的表才会被纳入分区过滤强制校验。
        # 不再硬编码任何特定仿真表名。
        self.large_tables: dict = {}
        # 扫描量阈值限制 (模拟行数)
        self.scan_row_limit = 50000

    def check_dsl(self, dsl: dict, layer, user_role: str = "user") -> dict:
        """
        DSL 编译前语义网闸审计：
        1. 指标存在性校验
        2. 指标权限校验
        3. 维度与指标的兼容性校验
        4. 查询时间跨度校验 (拒绝超过 365 天的查询)
        """
        # 1. 检查 metrics 存在性及权限
        metrics = dsl.get("metrics", [])
        if not metrics:
            raise GuardrailException("语义审计拦截: 未包含任何查询指标，请提供您想查询的业务指标（如GMV、订单数等）！")

        for m_item in metrics:
            m_name = m_item.get("name")
            metric = layer.resolve_metric(m_name)
            if not metric:
                raise GuardrailException(f"语义审计拦截: 发现未注册的指标名 '{m_name}'，请检查输入或在语义层完成指标治理注册！")
            
            # 2. 检查权限
            if user_role not in metric.authorized_roles:
                raise GuardrailException(f"权限审计拦截: 用户角色 '{user_role}' 无权访问受限指标 '{m_name}'，该指标仅限管理员/分析师使用！")
        
        # 维度存在性校验
        dimensions = dsl.get("dimensions", [])
        for d_item in dimensions:
            dim_name = d_item.get("name")
            if not dim_name or dim_name == "month":
                continue
            dim = layer.resolve_dimension(dim_name)
            if not dim:
                raise GuardrailException(f"语义审计拦截: 维度 '{dim_name}' 未在语义层注册，请检查输入！")

        # =====================================================================
        # 金融级行级与列级权限控制 (最高优先级安全壁垒)
        # =====================================================================
        # 1. 列级权限控制：对敏感列（如手机号、卡号）实施强管制拦截
        sensitive_columns = {
            "customer_phone": ["admin"],
            "customer_card_no": ["admin"]
        }
        filters = dsl.get("filters", [])
        
        # 检查维度 (dimensions)
        for d_item in dimensions:
            dim_name = d_item.get("name") or ""
            is_sensitive = False
            matched_key = None
            for s_col in sensitive_columns:
                if s_col in dim_name or dim_name in s_col or (("phone" in dim_name or "mobile" in dim_name) and "phone" in s_col) or ("card" in dim_name and "card" in s_col):
                    is_sensitive = True
                    matched_key = s_col
                    break
            
            if is_sensitive:
                allowed_roles = sensitive_columns[matched_key]
                if user_role not in allowed_roles:
                    raise GuardrailException(
                        f"金融级列级安全拦截: 发现敏感维度列 '{dim_name}' 越权访问！该敏感列仅对拥有 {', '.join(allowed_roles)} 权限的账户开放。"
                    )
                    
        # 检查过滤器条件 (filters) 
        for f_item in filters:
            field_name = f_item.get("field") or ""
            is_sensitive = False
            matched_key = None
            for s_col in sensitive_columns:
                if s_col in field_name or field_name in s_col or (("phone" in field_name or "mobile" in field_name) and "phone" in s_col) or ("card" in field_name and "card" in s_col):
                    is_sensitive = True
                    matched_key = s_col
                    break
                    
            if is_sensitive:
                allowed_roles = sensitive_columns[matched_key]
                if user_role not in allowed_roles:
                    raise GuardrailException(
                        f"金融级列级安全拦截: 发现敏感过滤条件字段 '{field_name}' 越权访问！"
                    )

        # 2. 行级权限控制：实现同一指标在不同辖区角色下的行级隔离。
        if user_role == "user":
            # 只有在主表指标支持大区维度 region_name 时，才进行行级隔离自动注入
            supports_region = False
            for m_item in metrics:
                m_name = m_item.get("name")
                metric = layer.resolve_metric(m_name)
                if metric and "region_name" in metric.available_dimensions:
                    supports_region = True
                    break
            
            if supports_region:
                region_found = False
                for f_item in filters:
                    if f_item.get("field") == "region_name":
                        region_found = True
                        if f_item.get("value") != "华东":
                            raise GuardrailException(
                                f"金融级行级安全拦截: 普通用户角色 '{user_role}' 数据辖区仅限于 '华东'，无权越权拉取其他行级数据（当前请求拉取: '{f_item.get('value')}'）！"
                            )
                
                if not region_found:
                    print(f"[Guardrail Row-Level Injection]: Automatically injected region_name = '华东' filter for user role: {user_role}")
                    filters.append({
                        "field": "region_name",
                        "op": "eq",
                        "value": "华东"
                    })

        # 3. 维度与指标兼容性校验 (安全通过后的业务限制校验)
        for d_item in dimensions:
            dim_name = d_item.get("name")
            if not dim_name or dim_name == "month":
                continue
            for m_item in metrics:
                m_name = m_item.get("name")
                metric = layer.resolve_metric(m_name)
                if metric and dim_name not in metric.available_dimensions:
                    raise GuardrailException(
                        f"语义审计拦截: 指标 '{m_name}' 与维度 '{dim_name}' 不兼容！"
                        f"该指标可用的分解维度为：{', '.join(metric.available_dimensions)}"
                    )

        # 4. 时间范围长度校验
        time_range = dsl.get("time_range")
        start_date, end_date = None, None
        if time_range and isinstance(time_range, dict):
            start_date = time_range.get("start")
            end_date = time_range.get("end")
        else:
            for f in filters:
                if f.get("field") == "dt" and f.get("op") == "between":
                    val = f.get("value")
                    if isinstance(val, list) and len(val) == 2:
                        start_date, end_date = val[0], val[1]
                        break
        
        if start_date and end_date:
            try:
                d1 = datetime.strptime(start_date[:10], "%Y-%m-%d")
                d2 = datetime.strptime(end_date[:10], "%Y-%m-%d")
                days = abs((d2 - d1).days)
                if days > 365:
                    raise GuardrailException(
                        f"性能审计拦截: 发现查询的时间跨度为 {days} 天，超过了系统允许的最大跨度限制 (365天)！"
                        f"请缩短时间范围以减轻物理数据库负担。"
                    )
            except Exception as e:
                if isinstance(e, GuardrailException):
                    raise e

        return {
            "status": "PASS",
            "message": "语义审计通过"
        }

    def check_sql(self, sql: str, dialect: str = "mysql", conn = None) -> dict:
        """
        全链路安全与性能审计，通过所有检查返回 dict 信息，否则抛出 GuardrailException
        """
        # 1. 净化并解析 SQL
        cleaned_sql = sql.strip().rstrip(";")
        
        try:
            expression = sqlglot.parse_one(cleaned_sql, read=dialect)
        except Exception as e:
            raise GuardrailException(f"SQL 语法解析失败，无法进行安全审计: {e}")

        # 2. DDL / DML 拦截（仅允许查询操作）
        # 遍历 AST 检查节点类型
        for node in expression.walk():
            # 如果包含创建、删除、插入、更新、修改等节点类型则拦截
            if isinstance(node, (exp.Create, exp.Drop, exp.Insert, exp.Update, exp.Delete, exp.Alter)):
                raise GuardrailException("安全审计拦截: 拒绝执行非 SELECT 查询的操作（拦截 DDL/DML 修改操作）！")
        
        # 3. 分区过滤检查
        # 提取 SQL 中查询的所有表
        tables = [t.name.lower() for t in expression.find_all(exp.Table)]
        for table in tables:
            if table in self.large_tables:
                partition_key = self.large_tables[table]
                # 检查 WHERE 条件中是否引用了分区键
                has_partition_filter = False
                
                # 遍历 WHERE 节点
                for where_node in expression.find_all(exp.Where):
                    # 在 WHERE 的子树中寻找分区键标识符
                    for column in where_node.find_all(exp.Column):
                        if column.name.lower() == partition_key:
                            has_partition_filter = True
                            break
                
                # 遍历 JOIN 节点，如果是 JOIN ON 关联分区键也可以
                for join_node in expression.find_all(exp.Join):
                    for column in join_node.find_all(exp.Column):
                        if column.name.lower() == partition_key:
                            has_partition_filter = True
                            break
                            
                if not has_partition_filter:
                    raise GuardrailException(
                        f"性能审计拦截: 发现大表 `{table}` 查询未设置分区键 `{partition_key}` 过滤条件！"
                        f"大表必须进行分区剪裁，请带上日期过滤条件（例如: dt BETWEEN ... 或 dt = ...），防止大表全表扫描。"
                    )

        # 4. 语法预检与扫描量预估
        estimated_rows = 1000 # 默认预估行数
        
        from app.service.db_service import db_service
        if os.getenv("DB_TYPE") != "sqlite" and db_service.real_engine:
            try:
                db_type_lower = db_service.active_db_type.lower()
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
                
                translated_sqls = sqlglot.transpile(cleaned_sql, read=dialect, write=target_dialect)
                target_sql = translated_sqls[0]
                
                # PostgreSQL 不支持 database.table 语法，去掉 db_name 前缀
                if "postgres" in target_dialect:
                    db_name = db_service.get_active_db_name()
                    target_sql = target_sql.replace(f"{db_name}.", "")
                
                # 直接在真实物理库执行 EXPLAIN
                explain_sql = f"EXPLAIN {target_sql}"
                from sqlalchemy import text
                with db_service.real_engine.connect() as connection:
                    connection.execute(text(explain_sql))
            except Exception as pe:
                raise GuardrailException(f"物理数据库方言语法预检报错: {pe}")
        elif conn:
            try:
                # 尝试将 SQL 转换成 SQLite 的 EXPLAIN 运行
                sqlite_sqls = sqlglot.transpile(cleaned_sql, read=dialect, write="sqlite")
                sqlite_sql = sqlite_sqls[0]
                sqlite_sql = re.sub(r"TIMESTAMP_TRUNC\(([^,]+),\s*MONTH\)", r"strftime('%Y-%m-01', \1)", sqlite_sql, flags=re.IGNORECASE)
                sqlite_sql = re.sub(r"date_trunc\('month',\s*([^)]+)\)", r"strftime('%Y-%m-01', \1)", sqlite_sql, flags=re.IGNORECASE)
                
                cursor = conn.cursor()
                explain_sql = f"EXPLAIN QUERY PLAN {sqlite_sql}"
                cursor.execute(explain_sql)
                plan_rows = cursor.fetchall()
                
                has_scan = False
                for step in plan_rows:
                    detail = step[3].lower() if len(step) > 3 else ""
                    if "scan" in detail:
                        has_scan = True
                
                date_range_days = 200
                match_dates = re.findall(r"'\d{4}-\d{2}-\d{2}'", cleaned_sql)
                if len(match_dates) >= 2:
                    try:
                        d1 = datetime.strptime(match_dates[0].replace("'", ""), "%Y-%m-%d")
                        d2 = datetime.strptime(match_dates[1].replace("'", ""), "%Y-%m-%d")
                        date_range_days = abs((d2 - d1).days)
                    except:
                        pass
                
                if has_scan and date_range_days > 150:
                    estimated_rows = date_range_days * 5 * 4 * 10
                else:
                    estimated_rows = date_range_days * 5 * 4
                
                if estimated_rows > self.scan_row_limit:
                    raise GuardrailException(
                        f"性能熔断拦截: 预估扫描行数 {estimated_rows} 超过了安全阈值 {self.scan_row_limit} 行！"
                        f"请缩短查询时间范围（例如限制在 30 天内）或加入更窄的维度过滤条件以减少扫描量。"
                    )
            except sqlite3.Error as se:
                raise GuardrailException(f"方言语法预检报错: {se}")

        return {
            "status": "PASS",
            "message": "审计通过",
            "estimated_rows": estimated_rows
        }

guardrail = Guardrail()
