# -*- coding: utf-8 -*-
"""
智能问数黄金回归评估测试套件 (Evaluation & Regression Suite)
用于验证系统在 问答准确率、多轮指代改写、确定性时间、列级/行级权限拦截、图表自适应等维度的正确性。
目标：证明系统准确率达 98% 以上。
"""
import sys
import os
import json

# 加入 PYTHONPATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["DB_TYPE"] = "sqlite"
from app.service.db_service import db_service
db_service.real_engine = None  # 强行断开物理数据库

# 在语义层自动发现前，动态构建回归测试所依赖的电商与文章系统仿真表
conn = db_service.conn
cursor = conn.cursor()
cursor.execute("DROP TABLE IF EXISTS dws_trade_order_daily")
cursor.execute("DROP TABLE IF EXISTS dim_region")
cursor.execute("DROP TABLE IF EXISTS dim_goods")
cursor.execute("DROP TABLE IF EXISTS articles")
cursor.execute("DROP TABLE IF EXISTS article_history")
cursor.execute("DROP TABLE IF EXISTS user_memory")

cursor.execute("""
CREATE TABLE dws_trade_order_daily (
    dt TEXT,
    region_id TEXT,
    goods_id TEXT,
    category_name TEXT,
    gmv REAL,
    refund_amount REAL,
    order_count INTEGER
)
""")
cursor.execute("""
CREATE TABLE dim_region (
    region_id TEXT,
    region_name TEXT
)
""")
cursor.execute("""
CREATE TABLE dim_goods (
    goods_id TEXT,
    goods_name TEXT
)
""")
cursor.execute("""
CREATE TABLE articles (
    id INTEGER PRIMARY KEY,
    title TEXT,
    content TEXT,
    source_platform TEXT,
    created_at TEXT,
    status TEXT,
    phone TEXT
)
""")
cursor.execute("""
CREATE TABLE article_history (
    id INTEGER PRIMARY KEY,
    title TEXT,
    content TEXT,
    category_name TEXT,
    version_num INTEGER,
    created_at TEXT
)
""")
cursor.execute("""
CREATE TABLE user_memory (
    id INTEGER PRIMARY KEY,
    question TEXT,
    error_message TEXT
)
""")

# 插入几条支撑回归断言的仿真样例数据
cursor.execute("INSERT INTO dim_region VALUES ('1', '华东'), ('2', '华北')")
cursor.execute("INSERT INTO dws_trade_order_daily (dt, region_id, goods_id, category_name, gmv, refund_amount, order_count) VALUES ('2026-07-12', '1', '101', '服饰', 1000.0, 100.0, 5)")
cursor.execute("INSERT INTO dws_trade_order_daily (dt, region_id, goods_id, category_name, gmv, refund_amount, order_count) VALUES ('2026-07-12', '2', '102', '食品', 500.0, 0.0, 2)")
cursor.execute("INSERT INTO articles (id, title, content, source_platform, created_at, status) VALUES (1, '测试文章', '内容', '微信', '2026-07-12', 'published')")
cursor.execute("INSERT INTO article_history (id, title, content, category_name, version_num, created_at) VALUES (1, '文章历史', '内容', '技术', 1, '2026-07-12')")
conn.commit()

# 执行实际依赖导入以触发语义层自动发现与注册
from app.service.ask_agent import ask_agent
from app.service.vector_service import vector_service
from app.service.guardrail import guardrail, GuardrailException

# 黄金测试用例定义
GOLD_TEST_SUITE = [
    {
        "id": "CASE-01",
        "description": "列值强索引映射与指标提取 (华东区退款额)",
        "question": "华东区昨天的退款额是多少",
        "user": "admin",
        "role": "admin",
        "verify": lambda res: res["success"] is True and "refund_amount" in res["details"]["sql"].lower() and "region_name" in res["details"]["sql"].lower()
    },
    {
        "id": "CASE-02",
        "description": "确定性相对时间归一化计算 (上个月退款额)",
        "question": "上个月退款额是多少",
        "user": "admin",
        "role": "admin",
        "verify": lambda res: res["success"] is True and any(f.get("field") == "dt" and f.get("op") == "between" for f in res["details"]["filters"])
    },
    {
        "id": "CASE-03",
        "description": "多轮上下文指代消解与改写 (那前三名品类呢)",
        "pre_question": "华东区昨天的销售额是多少",
        "question": "那前三名的品类呢",
        "user": "tester",
        "role": "analyst",
        "verify": lambda res: res["success"] is True and "category_name" in res["details"]["sql"].lower() and "limit" in res["details"]["sql"].lower()
    },
    {
        "id": "CASE-04",
        "description": "金融级列级权限拦截 (非管理员禁止查敏感词手机号)",
        "question": "帮我拉一下昨天有交易的客户手机号明细",
        "user": "normal_user",
        "role": "user",
        # 验证是否拦截且错误文本指向敏感列拦截
        "verify": lambda res: res["success"] is False and "金融级列级安全拦截" in res["error"]
    },
    {
        "id": "CASE-05",
        "description": "金融级行级权限隔离校验 (普通用户不带过滤自动注入行隔离)",
        "question": "昨天总交易额是多少",
        "user": "normal_user",
        "role": "user",
        # 验证是否成功返回且自动注入了“华东”行限制条件
        "verify": lambda res: res["success"] is True and any(f.get("field") == "region_name" and f.get("value") == "华东" for f in res["details"]["filters"])
    },
    {
        "id": "CASE-06",
        "description": "金融级行级跨区越权强力阻断 (普通用户不能查华北数据)",
        "question": "华北区昨天的交易额是多少",
        "user": "normal_user",
        "role": "user",
        # 验证行权限拦截报错
        "verify": lambda res: res["success"] is False and "金融级行级安全拦截" in res["error"]
    },
    {
        "id": "CASE-07",
        "description": "歧义消歧主动澄清熔断 (越界模糊词食堂消费额)",
        "question": "华东区过去30天食堂消费额是多少",
        "user": "admin",
        "role": "admin",
        # 验证是否熔断，并返回澄清结构
        "verify": lambda res: res["success"] is False and res.get("clarification", {}).get("need_clarification") is True
    },
    {
        "id": "CASE-08",
        "description": "智能可视化自适应饼图归并排序 (各品类销售占比)",
        "question": "各品类最近30天交易额",
        "user": "admin",
        "role": "admin",
        # 检验是否返回了饼图，并且饼图标题和结构渲染正确
        "verify": lambda res: res["success"] is True and res.get("chart", {}).get("type") == "pie"
    },
    {
        "id": "CASE-09",
        "description": "内容运营文章分类篇数统计 (新行业数据源识别与分析)",
        "question": "帮我分析article_history 表里每类文章分别有多少篇",
        "user": "admin",
        "role": "admin",
        # 验证是否成功返回数据，并且 SQL 包含 article_history 表，聚合包含 COUNT
        "verify": lambda res: res["success"] is True and "article_history" in res["details"]["sql"].lower() and ("count" in res["details"]["sql"].lower())
    },
    {
        "id": "CASE-10",
        "description": "运行时除零保护安全网闸校验与自纠错修复测试",
        "question": "各品类退款额除以交易额的比率",
        "user": "admin",
        "role": "admin",
        # 验证是否纠错成功，且生成的 SQL 中含有 nullif
        "verify": lambda res: res["success"] is True and "nullif" in res["details"]["sql"].lower()
    },
    {
        "id": "CASE-11",
        "description": "多对多无主外键 JOIN 校验拦截测试",
        "question": "帮我把article表和user_memory表进行不带外键等值连接",
        "user": "admin",
        "role": "admin",
        # 验证是否拦截且错误文本指向关联条件
        "verify": lambda res: res["success"] is False and "安全审计拦截" in res["error"]
    }
]

def run_evaluation_suite():
    print("=" * 80)
    print(" 🚀 智能问数黄金回归评估测试套件 (NL2SQL Regression Suite V2.0) 正在执行中...")
    print("=" * 80)
    
    passed_count = 0
    total_count = len(GOLD_TEST_SUITE)
    
    # 模拟环境设置
    os.environ["DB_TYPE"] = "sqlite"
    os.environ["MOCK_LLM"] = "true"
    
    for case in GOLD_TEST_SUITE:
        print(f"\n👉 [Running] {case['id']}: {case['description']}")
        ask_agent.user_sessions.clear()
        ask_agent.user_history_questions.clear()
        
        # 1. 模拟历史会话注入 (针对多轮改写用例)
        if "pre_question" in case:
            ask_agent.user_history_questions[case["user"]] = [case["pre_question"]]
        else:
            ask_agent.user_history_questions[case["user"]] = []
            
        try:
            # 2. 调用核心问答通道
            response = ask_agent.ask(
                question=case["question"],
                dialect="doris",
                user=case["user"],
                role=case["role"]
            )
            
            # 3. 验证断言
            verdict = case["verify"](response)
            if verdict:
                print(f"✅ [SUCCESS] {case['id']} 测试通过！")
                passed_count += 1
            else:
                print(f"❌ [FAILURE] {case['id']} 结果断言未通过！")
                print("Response Output:", json.dumps(response, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"💥 [ERROR] {case['id']} 执行时发生未捕获异常: {e}")
            
    # 计算准确率
    accuracy = (passed_count / total_count) * 100
    print("\n" + "=" * 80)
    print(" 📊 评估回归报告 (Evaluation Report Summary)")
    print(f"  - 总案例数 (Total Cases): {total_count}")
    print(f"  - 通过案例数 (Passed Cases): {passed_count}")
    print(f"  - 自动评估准确率 (Accuracy Score): {accuracy:.2f}%")
    print("=" * 80)
    
    if accuracy >= 98.0:
        print("🎉 [VERDICT] 回归通过！系统指标解析与权限隔离性能满足金融级生产要求！")
        return True
    else:
        print("⚠️ [VERDICT] 准确率未达标，请检查错误用例！")
        return False

if __name__ == "__main__":
    run_evaluation_suite()
