"""Shared, deterministic source fixture for local SQLite and PostgreSQL migration.

This module has no singleton or connection side effects. PostgreSQL is seeded from
these source rows once; subsequent starts preserve all database data.
"""
from datetime import datetime, timedelta
import sqlite3

FIXTURE_TABLES = (
    "dws_trade_order_daily", "dim_region", "dim_goods", "categories", "articles",
    "article_history", "dws_audio_album_daily", "dws_audio_member_trade_daily",
    "dim_audio_anchor",
)


def initialize_fixture(conn: sqlite3.Connection, reference_date: datetime | None = None) -> None:
    cursor = conn.cursor()
    today = reference_date or datetime.now()
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    recent_day = (today - timedelta(days=10)).strftime("%Y-%m-%d")
    last_month = (today - timedelta(days=35)).strftime("%Y-%m-%d")

    # 1. 通用交易轻度汇总表与维表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dws_trade_order_daily (
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
    CREATE TABLE IF NOT EXISTS dim_region (
        region_id TEXT,
        region_name TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dim_goods (
        goods_id TEXT,
        goods_name TEXT
    )
    """)
    # 文章分类维表：内容域的分解维度来源，使 articles 记录数支持按分类下钻。
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY,
        name TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY,
        title TEXT,
        content TEXT,
        source_platform TEXT,
        created_at TEXT,
        status TEXT,
        phone TEXT,
        category_id INTEGER
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS article_history (
        id INTEGER PRIMARY KEY,
        title TEXT,
        category_name TEXT,
        dt TEXT
    )
    """)

    # 2. 听书问数业务表 (ListenBook Domain: 专辑播放、会员订阅与主播维表)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dws_audio_album_daily (
        dt TEXT,
        album_id TEXT,
        album_name TEXT,
        category_name TEXT,
        anchor_name TEXT,
        play_count INTEGER,
        play_duration_seconds INTEGER,
        completion_rate REAL
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dws_audio_member_trade_daily (
        dt TEXT,
        plan_name TEXT,
        category_name TEXT,
        audio_gmv REAL,
        audio_refund_amount REAL,
        paid_users INTEGER
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dim_audio_anchor (
        anchor_id TEXT,
        anchor_name TEXT,
        level TEXT,
        region_name TEXT
    )
    """)

    # 检查并幂等插入电商数据
    cursor.execute("SELECT COUNT(1) FROM dws_trade_order_daily")
    if cursor.fetchone()[0] == 0:
        regions = [("1", "华东"), ("2", "华北"), ("3", "华南"), ("4", "华中")]
        cursor.executemany("INSERT INTO dim_region VALUES (?, ?)", regions)

        goods = [("G01", "旗舰智能手机"), ("G02", "冷链时令水果"), ("G03", "全棉四件套")]
        cursor.executemany("INSERT INTO dim_goods VALUES (?, ?)", goods)

        trade_data = [
            (yesterday, "1", "G01", "数码3C", 50000.0, 3200.0, 120),
            (yesterday, "1", "G02", "生鲜食品", 35000.0, 1800.0, 95),
            (yesterday, "2", "G01", "数码3C", 42000.0, 2100.0, 100),
            (yesterday, "3", "G03", "家居家纺", 28000.0, 900.0, 60),
            (recent_day, "1", "G01", "数码3C", 68000.0, 4100.0, 150),
            (recent_day, "2", "G02", "生鲜食品", 45000.0, 1200.0, 110),
            (last_month, "1", "G01", "数码3C", 40000.0, 1500.0, 80)
        ]
        cursor.executemany("INSERT INTO dws_trade_order_daily VALUES (?, ?, ?, ?, ?, ?, ?)", trade_data)

        categories_data = [
            (1, "技术架构"), (2, "数据治理"), (3, "行业实践"), (4, "算法模型")
        ]
        cursor.executemany("INSERT INTO categories VALUES (?, ?)", categories_data)

        articles_data = [
            (1, "大模型智能问数系统架构设计", "全文阐述 NL2SQL 全链路设计", "WeChat", yesterday, "PUBLISHED", "13800138000", 1),
            (2, "湖仓一体架构实战指南", "Iceberg 与 Doris 深度集成", "Zhihu", yesterday, "PUBLISHED", "13900139000", 1),
            (3, "淘宝百亿补贴多维异动归因实践", "业务根因自动下钻定位", "Juejin", recent_day, "PUBLISHED", "13700137000", 3),
            (4, "指标口径统一与语义层落地", "一次定义、处处复用的指标治理", "WeChat", recent_day, "PUBLISHED", "13600136000", 2),
            (5, "数据血缘采集与影响面分析", "从解析 SQL 到全链路血缘图", "Zhihu", recent_day, "PUBLISHED", "13500135000", 2),
            (6, "数据质量稽核规则体系建设", "空值、波动与一致性校验落地", "Juejin", last_month, "DRAFT", "13400134000", 2),
            (7, "Schema Linking 精排模型实践", "候选表列召回后的重排序策略", "Juejin", last_month, "PUBLISHED", "13300133000", 4),
            (8, "向量召回与倒排检索的混合方案", "稀疏与稠密检索融合调优", "Zhihu", recent_day, "PUBLISHED", "13200132000", 4),
            (9, "听书业务会员增长复盘", "专辑分发与订阅转化拆解", "WeChat", yesterday, "PUBLISHED", "13100131000", 3),
            (10, "实时数仓分层规范", "ODS 到 ADS 的落地边界", "WeChat", last_month, "PUBLISHED", "13000130000", 1),
            (11, "查询网闸与行列级权限设计", "语义审计与安全拦截实现", "Juejin", yesterday, "DRAFT", "13911139110", 2),
            (12, "反馈飞轮驱动的问数效果迭代", "从用户纠错到样本回流", "Zhihu", recent_day, "PUBLISHED", "13822138220", 4)
        ]
        cursor.executemany("INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)", articles_data)

        # 文章发布流水：按业务日期保留分类快照，供按天/按分类的内容域问数。
        category_names = dict(categories_data)
        history_rows = []
        for offset in range(1, 61):
            day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            for index in range((offset % 3) + 1):
                article = articles_data[(offset * 2 + index) % len(articles_data)]
                history_rows.append((len(history_rows) + 1, article[1],
                                     category_names[article[7]], day))
        cursor.executemany("INSERT INTO article_history VALUES (?, ?, ?, ?)", history_rows)

    # 检查并幂等插入听书业务数据 (Listen-Book)
    cursor.execute("SELECT COUNT(1) FROM dim_audio_anchor")
    if cursor.fetchone()[0] == 0:
        anchors = [
            ("A01", "主播晓月", "金牌主播", "华东区"),
            ("A02", "主播大飞", "资深主播", "华北区"),
            ("A03", "主播小青", "新锐主播", "华南区")
        ]
        cursor.executemany("INSERT INTO dim_audio_anchor VALUES (?, ?, ?, ?)", anchors)

    cursor.execute("SELECT COUNT(1) FROM dws_audio_album_daily")
    if cursor.fetchone()[0] == 0:
        audio_album_data = [
            (yesterday, "ALB01", "三体：全景有声剧", "科幻有声", "主播晓月", 125000, 3600000, 0.78),
            (yesterday, "ALB02", "明朝那些事儿", "历史军事", "主播大飞", 98000, 2800000, 0.82),
            (yesterday, "ALB03", "雪中悍刀行", "玄幻武侠", "主播晓月", 86000, 2400000, 0.71),
            (yesterday, "ALB04", "原则：人生与工作", "商业财经", "主播小青", 45000, 1100000, 0.65),
            (yesterday, "ALB05", "郭德纲相声精选", "相声曲艺", "主播大飞", 67000, 1900000, 0.85),
            (recent_day, "ALB01", "三体：全景有声剧", "科幻有声", "主播晓月", 112000, 3200000, 0.76),
            (recent_day, "ALB02", "明朝那些事儿", "历史军事", "主播大飞", 89000, 2500000, 0.80),
            (recent_day, "ALB05", "郭德纲相声精选", "相声曲艺", "主播大飞", 59000, 1600000, 0.83)
        ]
        cursor.executemany("INSERT INTO dws_audio_album_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)", audio_album_data)

    cursor.execute("SELECT COUNT(1) FROM dws_audio_member_trade_daily")
    if cursor.fetchone()[0] == 0:
        audio_member_data = [
            (yesterday, "年度畅听VIP", "科幻有声", 88000.0, 4200.0, 450),
            (yesterday, "月度随心听", "历史军事", 36000.0, 1500.0, 320),
            (yesterday, "连续包季VIP", "玄幻武侠", 49000.0, 2800.0, 290),
            (yesterday, "连续包月VIP", "相声曲艺", 28000.0, 950.0, 210),
            (recent_day, "年度畅听VIP", "科幻有声", 75000.0, 1800.0, 380),
            (recent_day, "月度随心听", "历史军事", 32000.0, 900.0, 280)
        ]
        cursor.executemany("INSERT INTO dws_audio_member_trade_daily VALUES (?, ?, ?, ?, ?, ?)", audio_member_data)

    # Complete, reproducible demo periods make comparisons meaningful.  These are
    # synthetic source rows (never precomputed answers), queried by the same SQL
    # path as a configured warehouse. Existing demonstration days are preserved.
    for table, numeric_start in (("dws_trade_order_daily", 4),
                                 ("dws_audio_album_daily", 5),
                                 ("dws_audio_member_trade_daily", 3)):
        existing_days = {row[0] for row in cursor.execute(f"SELECT DISTINCT dt FROM {table}")}
        template = cursor.execute(f"SELECT * FROM {table} WHERE dt = ?", (yesterday,)).fetchall()
        for offset in range(2, 401):
            day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            if day in existing_days:
                continue
            rows = []
            for index, source in enumerate(template):
                row = list(source)
                row[0] = day
                # A modest weekday/period trend, with distinct slice changes.
                scale = (0.70 if offset > 30 else 0.88) + ((offset + index * 3) % 7) * 0.025
                for column in range(numeric_start, len(row)):
                    if table == "dws_audio_album_daily" and column == 7:
                        continue  # completion_rate is a rate, not a count.
                    value = row[column] * scale
                    row[column] = int(value) if isinstance(source[column], int) else round(value, 2)
                rows.append(row)
            if rows:
                placeholders = ",".join("?" for _ in rows[0])
                cursor.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)

    conn.commit()

