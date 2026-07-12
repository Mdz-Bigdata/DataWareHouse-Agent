# -*- coding: utf-8 -*-
import os
import json
from datetime import datetime

# NOTE: 数仓开发 Agent 协作协调器。根据选定的数据源和 SQL 引擎驱动 7 大 Agent 的开发工作流仿真并产出多引擎适配代码。

class DevAgentCoordinator:
    def __init__(self):
        self.workspace_dir = "/Users/mindezhi/DataWareHouse-Agent"

    def start_dev_workflow(self, requirement: str, datasource: str = "doris", sql_engine: str = "doris") -> dict:
        """
        根据输入需求、数据源和 SQL 执行引擎，执行多 Agent 协作工作流。
        """
        print(f"\n[DevAgentCoordinator] Starting workflow: {requirement} (Source: {datasource}, Engine: {sql_engine})")
        
        datasource = datasource.lower().strip()
        sql_engine = sql_engine.lower().strip()

        # 动态创建基于数据源的子目录
        init_dir = os.path.join(self.workspace_dir, "init", datasource)
        etl_dir = os.path.join(self.workspace_dir, "etl", datasource)
        job_dir = os.path.join(self.workspace_dir, "job", "batch", "pipeline")
        docs_dir = os.path.join(self.workspace_dir, "docs", "data-model")

        for d in [init_dir, etl_dir, job_dir, docs_dir]:
            os.makedirs(d, exist_ok=True)

        biz_domain = "trade"
        if "库存" in requirement:
            biz_domain = "inventory"
        elif "用户" in requirement:
            biz_domain = "user"
            
        table_base = "order_summary" if biz_domain == "trade" else f"{biz_domain}_status"
        table_name = f"dws_{biz_domain}_{table_base}_daily"
        db_name = "dw_store"

        # ---------------- Phase 1: 接收需求 ----------------
        phase1_log = {
            "agent": "@data-warehouse",
            "action": "接收需求并路由任务",
            "input": f"需求: {requirement} | 数据源: {datasource} | SQL引擎: {sql_engine}",
            "output": {
                "summary": f"已接收需求。选定物理数据源: {datasource}，加工执行引擎: {sql_engine}。",
                "route_decision": f"调度 @data-warehouse-architect 进行分层决策，派发 {datasource} DDL 设计至 modeler，派发 {sql_engine} ETL 开发至 data-engineer。"
            }
        }

        # ---------------- Phase 2: 数仓设计 (DDL) ----------------
        # 依据不同数据源拼装 DDL
        if datasource == "clickhouse":
            ddl_sql = f"""-- =====================================================================
-- 表名称: {db_name}.{table_name}
-- 描述: 电商交易日汇总表 (ClickHouse Model)
-- 物理数据源: ClickHouse
-- Modeler: @data-warehouse-modeler
-- =====================================================================

CREATE TABLE IF NOT EXISTS {db_name}.{table_name} (
    dt Date COMMENT '分区日期 (YYYY-MM-DD)',
    region_id Int32 COMMENT '区域 ID',
    region_name String COMMENT '区域名称',
    category_name String COMMENT '品类名称',
    gmv Float64 COMMENT '总交易额 (GMV)',
    order_count Int64 COMMENT '订单总量',
    refund_amount Float64 COMMENT '总退款金额',
    refund_count Int64 COMMENT '总退款量'
) ENGINE = MergeTree()
PARTITION BY dt
ORDER BY (region_id, category_name)
SETTINGS index_granularity = 8192;
"""
        else:
            # Doris / StarRocks 建表语法
            ddl_sql = f"""-- =====================================================================
-- 表名称: {db_name}.{table_name}
-- 描述: 电商交易日汇总表 (Doris/StarRocks Model)
-- 物理数据源: {datasource.upper()}
-- Modeler: @data-warehouse-modeler
-- =====================================================================

CREATE TABLE IF NOT EXISTS {db_name}.{table_name} (
    dt DATE COMMENT "分区日期 (YYYY-MM-DD)",
    region_id INT COMMENT "区域 ID",
    region_name VARCHAR(50) COMMENT "区域名称",
    category_name VARCHAR(100) COMMENT "品类名称",
    gmv DOUBLE COMMENT "总交易额 (GMV)",
    order_count INT COMMENT "订单总量",
    refund_amount DOUBLE COMMENT "总退款金额",
    refund_count INT COMMENT "总退款量"
)
UNIQUE KEY(dt, region_id, category_name)
PARTITION BY RANGE(dt) (
    PARTITION p_202605 VALUES LESS THAN ("2026-06-01"),
    PARTITION p_202606 VALUES LESS THAN ("2026-07-01"),
    PARTITION p_202607 VALUES LESS THAN ("2026-08-01")
)
DISTRIBUTED BY HASH(region_id) BUCKETS 8
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1",
    "compression" = "zstd"
);
"""
        ddl_path = os.path.join(init_dir, f"{table_name}.sql")
        with open(ddl_path, "w", encoding="utf-8") as f:
            f.write(ddl_sql)

        architecture_doc = f"""# 数仓分层规划设计 ({datasource.upper()})

## 1. 架构目标
物理存储选定为 {datasource}，分层架构符合总体设计。

## 2. 物理拓扑
- 数据源: {datasource.upper()}
- 写入引擎: {sql_engine.upper()}
"""

        phase2_review = {
            "status": "APPROVED",
            "comments": f"表结构设计符合 {datasource.upper()} 建模规范，分区策略及命名合规。批准落盘 DDL。"
        }

        phase2_log = {
            "agent": "@data-warehouse-architect + @data-warehouse-modeler",
            "action": "数仓建模与表结构设计",
            "reviewer": "@data-warehouse-reviewer",
            "review_status": phase2_review["status"],
            "review_comments": phase2_review["comments"],
            "output": {
                "architecture_doc": architecture_doc,
                "ddl_file": f"init/{datasource}/{table_name}.sql",
                "ddl_content": ddl_sql
            }
        }

        # ---------------- Phase 3: 开发代码 (ETL) ----------------
        # 依据不同 SQL 引擎编写对应 ETL SQL
        if sql_engine == "flinksql":
            etl_sql = f"""-- =====================================================================
-- 模块名称: FlinkSQL 实时流计算作业 - {table_name}
-- 执行引擎: Flink
-- Data Engineer: @data-warehouse-data-engineer
-- =====================================================================

CREATE TEMPORARY TABLE temp_orders (
    created_at TIMESTAMP(3),
    region_id INT,
    category_name STRING,
    gmv DOUBLE,
    order_id STRING,
    WATERMARK FOR created_at AS created_at - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'orders-trade',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json'
);

INSERT INTO {db_name}.{table_name}
SELECT 
    TUMBLE_START(created_at, INTERVAL '1' DAY) AS dt,
    region_id,
    'ALL' AS region_name,
    category_name,
    SUM(gmv) AS gmv,
    COUNT(DISTINCT order_id) AS order_count,
    0.0 AS refund_amount,
    0 AS refund_count
FROM temp_orders
GROUP BY TUMBLE(created_at, INTERVAL '1' DAY), region_id, category_name;
"""
        elif sql_engine == "sparksql":
            etl_sql = f"""-- =====================================================================
-- 模块名称: SparkSQL 离线计算作业 - {table_name}
-- 执行引擎: Spark
-- 变量说明: 使用 ${{run_date}} 变量传入运行日期
-- Data Engineer: @data-warehouse-data-engineer
-- =====================================================================

SET spark.sql.sources.partitionOverwriteMode=dynamic;

INSERT OVERWRITE TABLE {db_name}.{table_name} PARTITION (dt)
SELECT 
    region_id,
    'ALL' AS region_name,
    category_name,
    SUM(gmv) AS gmv,
    COUNT(DISTINCT order_id) AS order_count,
    0.0 AS refund_amount,
    0 AS refund_count,
    TO_DATE(created_at) AS dt
FROM {db_name}.dwd_trade_order_detail
WHERE TO_DATE(created_at) = '${{run_date}}'
GROUP BY region_id, category_name, TO_DATE(created_at);
"""
        elif sql_engine == "postgresql":
            etl_sql = f"""-- =====================================================================
-- 模块名称: PostgreSQL ETL 事务更新脚本 - {table_name}
-- 执行引擎: PostgreSQL
-- 变量说明: 使用 ${{run_date}} 变量传入运行日期
-- Data Engineer: @data-warehouse-data-engineer
-- =====================================================================

BEGIN;
DELETE FROM {db_name}.{table_name} WHERE dt = '${{run_date}}';

INSERT INTO {db_name}.{table_name}
SELECT 
    DATE(created_at) AS dt,
    region_id,
    'ALL' AS region_name,
    category_name,
    SUM(gmv) AS gmv,
    COUNT(DISTINCT order_id) AS order_count,
    0.0 AS refund_amount,
    0 AS refund_count
FROM {db_name}.dwd_trade_order_detail
WHERE created_at >= '${{run_date}} 00:00:00' AND created_at <= '${{run_date}} 23:59:59'
GROUP BY DATE(created_at), region_id, category_name;
COMMIT;
"""
        else:
            # Doris / StarRocks / ClickHouse 默认天级增量 ETL
            etl_sql = f"""-- =====================================================================
-- 模块名称: ETL - {table_name}
-- 执行引擎: {sql_engine.upper()}
-- 上游数据源: dwd_{biz_domain}_order_detail
-- 变量说明: 使用 ${{run_date}} 变量传入运行日期
-- Data Engineer: @data-warehouse-data-engineer
-- =====================================================================

WITH daily_orders AS (
    SELECT 
        DATE(created_at) AS dt,
        region_id,
        category_name,
        gmv,
        order_id
    FROM {db_name}.dwd_{biz_domain}_order_detail
    WHERE created_at >= '${{run_date}} 00:00:00'
      AND created_at <= '${{run_date}} 23:59:59'
),
daily_refunds AS (
    SELECT
        DATE(refund_time) AS dt,
        region_id,
        category_name,
        refund_amount,
        refund_id
    FROM {db_name}.dwd_{biz_domain}_refund_detail
    WHERE refund_time >= '${{run_date}} 00:00:00'
      AND refund_time <= '${{run_date}} 23:59:59'
)
INSERT INTO {db_name}.{table_name}
SELECT 
    COALESCE(o.dt, r.dt) AS dt,
    COALESCE(o.region_id, r.region_id) AS region_id,
    dim.region_name,
    COALESCE(o.category_name, r.category_name) AS category_name,
    SUM(COALESCE(o.gmv, 0)) AS gmv,
    COUNT(DISTINCT o.order_id) AS order_count,
    SUM(COALESCE(r.refund_amount, 0)) AS refund_amount,
    COUNT(DISTINCT r.refund_id) AS refund_count
FROM daily_orders o
FULL JOIN daily_refunds r 
    ON o.region_id = r.region_id 
   AND o.category_name = r.category_name 
   AND o.dt = r.dt
LEFT JOIN {db_name}.dim_region dim 
    ON COALESCE(o.region_id, r.region_id) = dim.region_id
GROUP BY 
    dt,
    region_id,
    region_name,
    category_name;
"""
        etl_path = os.path.join(etl_dir, f"etl_{table_name}.sql")
        with open(etl_path, "w", encoding="utf-8") as f:
            f.write(etl_sql)

        phase3_review = {
            "status": "APPROVED",
            "comments": f"ETL 审计通过：符合 {sql_engine.upper()} 引擎脚本规范。无敏感用户名/密码硬编码，已使用变量动态传参。"
        }

        phase3_log = {
            "agent": "@data-warehouse-data-engineer",
            "action": f"ETL 逻辑与 {sql_engine.upper()} 编写",
            "reviewer": "@data-warehouse-reviewer",
            "review_status": phase3_review["status"],
            "review_comments": phase3_review["comments"],
            "output": {
                "etl_file": f"etl/{datasource}/etl_{table_name}.sql",
                "etl_content": etl_sql
            }
        }

        # ---------------- Phase 4: 开发 DataArts Job 配置文件 ----------------
        job_type = "BATCH" if sql_engine != "flinksql" else "REAL_TIME"
        node_type_mapping = {
            "flinksql": "FlinkSQL",
            "sparksql": "SparkSQL",
            "postgresql": "PostgreSql",
            "clickhouse": "ClickHouseSql",
            "doris": "DorisSql",
            "starrocks": "StarRocksSql"
        }
        
        node_type = node_type_mapping.get(sql_engine, "DorisSql")

        job_json = {
            "name": f"batch_{table_name}_job",
            "type": job_type,
            "scheduler": {
                "cron": "0 2 * * *",
                "startTime": "2026-07-09T00:00:00Z",
                "retryPolicy": {
                    "maxAttempts": 3,
                    "interval": 300
                }
            },
            "nodes": [
                {
                    "name": f"node_{table_name}",
                    "type": node_type,
                    "properties": {
                        "scriptName": f"etl_{table_name}.sql",
                        "scriptArgs": [
                            {"name": "run_date", "value": "#{Job.planTime(yyyy-MM-dd)}"}
                        ] if sql_engine != "flinksql" else [],
                        "connectionName": f"{datasource.upper()}-DW"
                    }
                }
            ]
        }
        
        job_path = os.path.join(job_dir, f"batch_{table_name}_job.json")
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job_json, f, ensure_ascii=False, indent=2)

        phase4_log = {
            "skill": "dataarts-batch-job",
            "action": "生成 DataArts Job 配置文件",
            "output": {
                "job_file": f"job/batch/pipeline/batch_{table_name}_job.json",
                "job_content": json.dumps(job_json, ensure_ascii=False, indent=2)
            }
        }

        # ---------------- Phase 5: 上传脚本到 DataArts ----------------
        phase5_log = {
            "skill": "dataarts-studio-scripts",
            "action": "扫描本地并自动上传 SQL 脚本",
            "output": {
                "status": "SUCCESS",
                "uploaded_files": [
                    f"etl/{datasource}/etl_{table_name}.sql"
                ],
                "dataarts_directory": f"/{datasource}/{biz_domain}/{table_name}",
                "connection_mapping": f"{datasource.upper()}-DW"
            }
        }

        # ---------------- Phase 6: 文档生成 (数据模型文档) ----------------
        model_doc = f"""# 数据模型文档 - {db_name}.{table_name}

## 1. 概览
- **所属数据库**: {db_name}
- **表名称**: {table_name}
- **数仓层级**: DWS
- **物理数据源**: {datasource.upper()}
- **加工引擎**: {sql_engine.upper()}

## 2. ETL 说明
- **调度频率**: 天级
- **写入方式**: {"增量写入 (INSERT INTO)" if sql_engine != "sparksql" else "动态覆盖 (INSERT OVERWRITE)"}

## 3. 字段定义
| 编号 | 字段名 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | dt | DATE | NULL | 分区日期 (YYYY-MM-DD) |
| 2 | region_id | INT | NULL | 区域 ID |
| 3 | region_name | VARCHAR(50) | NULL | 区域名称 |
| 4 | category_name | VARCHAR(100) | NULL | 品类名称 |
| 5 | gmv | DOUBLE | 0.0 | 总交易额 (GMV) |
| 6 | order_count | INT | 0 | 订单总量 |
| 7 | refund_amount | DOUBLE | 0.0 | 总退款金额 |
| 8 | refund_count | INT | 0 | 总退款量 |

## 4. 建表 DDL
```sql
{ddl_sql}
```
"""
        doc_path = os.path.join(docs_dir, f"{db_name}.{table_name}.md")
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(model_doc)

        readme_content = f"""# 数据模型总览索引

## 数据流向图 (Mermaid)
```mermaid
graph TD
    ODS[ods_trade_orders] --> DWD_Order[dwd_trade_order_detail]
    DWD_Order -->|{sql_engine.upper()}| DWS_Summary[{table_name}]
```

## 数据表清单
- [DWS 层级] [{table_name} 数据模型文档](file://{doc_path})
"""
        readme_path = os.path.join(docs_dir, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(readme_content)

        phase6_log = {
            "agent": "@data-warehouse-doc-writer",
            "action": "生成数据模型文档",
            "output": {
                "doc_file": f"docs/data-model/{db_name}.{table_name}.md",
                "readme_file": "docs/data-model/README.md",
                "doc_preview": model_doc[:500] + "\n...(剩余内容详见文档)"
            }
        }

        # ---------------- Phase 7: 更新 DataArts 上的 Job ----------------
        phase7_log = {
            "skill": "dataarts-job-uploader",
            "action": "上传 Job 配置文件到华为云 DataArts",
            "output": {
                "status": "SUCCESS",
                "job_name": f"batch_{table_name}_job",
                "project_id": "cn-north-4_dw_project",
                "api_endpoint": "POST /v1/jobs",
                "log": f"已在 DataArts 上仿真成功绑定 {datasource.upper()}-DW 连接，并发布单任务作业。"
            }
        }

        # 部署清单
        deployment_checklist = [
            {"id": 1, "step": "需求分析和任务路由", "agent": "@data-warehouse", "done": True},
            {"id": 2, "step": "架构设计和表结构（DDL）", "agent": "architect + modeler", "done": True},
            {"id": 3, "step": "设计审查", "agent": "@data-warehouse-reviewer", "done": True},
            {"id": 4, "step": "ETL SQL 开发完成并本地验证通过", "agent": "@data-warehouse-data-engineer", "done": True},
            {"id": 5, "step": "代码审查", "agent": "@data-warehouse-reviewer", "done": True},
            {"id": 6, "step": "SQL 脚本已上传到 DataArts", "agent": "dataarts-studio-scripts skill", "done": True},
            {"id": 7, "step": "Job 配置文件已生成", "agent": "dataarts-batch-job skill", "done": True},
            {"id": 8, "step": "DataArts 上的 Job 已更新", "agent": "dataarts-job-uploader skill", "done": True},
            {"id": 9, "step": "调度配置是否正确", "agent": "@data-warehouse", "done": True},
            {"id": 10, "step": "数据模型文档生成", "agent": "@data-warehouse-doc-writer", "done": True}
        ]

        return {
            "success": True,
            "table_name": table_name,
            "db_name": db_name,
            "phases": [
                phase1_log,
                phase2_log,
                phase3_log,
                phase4_log,
                phase5_log,
                phase6_log,
                phase7_log
            ],
            "checklist": deployment_checklist
        }

dev_agent_coordinator = DevAgentCoordinator()
