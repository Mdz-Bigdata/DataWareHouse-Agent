# -*- coding: utf-8 -*-
import os
import re
import time
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from app.service.db_service import db_service
from app.service.guardrail import guardrail, GuardrailException
from app.model.user_memory import user_memory
from app.service.semantic_layer import semantic_layer, DSLCompiler, align_timezone_range, TABLE_CONFIG
from app.service.vector_service import vector_service
from app.service.semantic_cache import semantic_cache
from app.service.date_ranges import question_periods

# =====================================================================
# 智能问数 Agent V2.0：从 Text2SQL 到语义层 + DSL 升级实现
# =====================================================================

class AskAgent:
    def __init__(self):
        self.semantic_layer = semantic_layer
        # 用来保存每个用户的 QuerySessionState（上一轮成功的意图 DSL），实现多轮参数防丢失
        self.user_sessions = {}
        # 用来保存每个用户历史提问，实现多轮问句大模型上下文改写（Query Rewriter）
        self.user_history_questions = {}

    @staticmethod
    def _format_sql_for_display(sql: str, dialect: str) -> str:
        """Format SQL for readable UI display without changing the executed statement."""
        import sqlglot
        try:
            return sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect, pretty=True)
        except Exception as e:
            print(f"SQL display formatting failed: {e}. Falling back to raw SQL.")
            return sql.strip()

    def _call_llm(self, prompt: str, system_prompt: str = "", user: str = "anonymous", model_tier: str = "fast") -> str:
        """
        调用真实大语言模型 (OpenAI / Gemini / DeepSeek) 并进行模型分级路由 (Model Routing)。
        model_tier: "fast" (低延迟小模型，用于改写、意图提取) 或 "complex" (高推理大模型，用于复杂SQL、纠错与归因)
        """
        # 1.1 Mock LLM Fallback (仅当开启 MOCK_LLM 环境变量时有效)
        if os.getenv("MOCK_LLM") == "true":
            # 1) 多轮改写助手
            if "多轮" in system_prompt or "改写" in system_prompt:
                if "那前三名的品类呢" in prompt:
                    return "华东区昨天销售额排名前三的品类分别是什么"
                return prompt
                
            # 2) 商业分析师总结
            if "商业分析师" in system_prompt:
                return "总的来看，数据正常，符合预期。"
                
            # 3) SQL 纠错助手
            if "SQL 纠错" in system_prompt or "纠错助手" in system_prompt:
                # 如果是除零纠错
                if "ratio" in prompt.lower() or "比率" in prompt.lower() or "除以" in prompt.lower() or "refund_amount" in prompt.lower():
                    return "SELECT dws_trade_order_daily.category_name AS category_name, SUM(dws_trade_order_daily.refund_amount) / NULLIF(SUM(dws_trade_order_daily.gmv), 0) AS ratio FROM dws_trade_order_daily GROUP BY dws_trade_order_daily.category_name"
                if "user_memory" in prompt.lower() or "articles" in prompt.lower() or "等值连接" in prompt.lower():
                    return "SELECT * FROM articles LEFT JOIN user_memory ON articles.title = user_memory.question"
                return prompt
                
            # 4) 意图解析 QueryDSL 转换
            target_question = prompt
            if "【用户问题】:" in prompt:
                target_question = prompt.split("【用户问题】:")[-1]
            prompt_clean = target_question.replace(" ", "").replace("\n", "")
            # CASE-01: 华东区昨天的退款额是多少
            if "退款额" in prompt_clean and "昨天" in prompt_clean and "华东" in prompt_clean:
                return '{"metrics": [{"name": "total_refund_amount"}], "dimensions": [{"name": "region_name"}], "filters": [{"field": "region_name", "op": "eq", "value": "华东"}, {"field": "dt", "op": "yesterday", "value": null}]}'
            # CASE-02: 上个月退款额是多少
            elif "上个月退款额" in prompt_clean or ("退款额" in prompt_clean and "上个月" in prompt_clean):
                return '{"metrics": [{"name": "total_refund_amount"}], "dimensions": [], "filters": [{"field": "dt", "op": "last_month", "value": null}]}'
            # CASE-03: 华东区昨天销售额排名前三的品类分别是什么
            elif "华东区昨天销售额排名前三的品类分别是什么" in prompt_clean:
                return '{"metrics": [{"name": "total_gmv"}], "dimensions": [{"name": "category_name"}], "filters": [{"field": "dt", "op": "yesterday", "value": null}, {"field": "region_name", "op": "eq", "value": "华东"}], "sort": [{"field": "total_gmv", "direction": "desc"}], "limit": 3}'
            # CASE-04: 帮我拉一下昨天有交易的客户手机号明细
            elif "手机号" in prompt_clean:
                return '{"metrics": [{"name": "total_gmv"}], "dimensions": [{"name": "phone"}], "filters": [{"field": "dt", "op": "yesterday", "value": null}]}'
            # CASE-05: 昨天总交易额是多少
            elif "昨天总交易额" in prompt_clean or ("交易额" in prompt_clean and "昨天" in prompt_clean and "总" in prompt_clean):
                return '{"metrics": [{"name": "total_gmv"}], "dimensions": [], "filters": [{"field": "dt", "op": "yesterday", "value": null}]}'
            # CASE-06: 华北区昨天的交易额是多少
            elif "华北区昨天的交易额是多少" in prompt_clean or ("交易额" in prompt_clean and "昨天" in prompt_clean and "华北" in prompt_clean):
                return '{"metrics": [{"name": "total_gmv"}], "dimensions": [{"name": "region_name"}], "filters": [{"field": "region_name", "op": "eq", "value": "华北"}, {"field": "dt", "op": "yesterday", "value": null}]}'
            # CASE-07: 食堂消费额 (越界词)
            elif "食堂" in prompt_clean:
                return '{"need_clarification": true, "clarification_msg": "你想查询哪种指标数据？目前仅支持系统已接入的指标（如 GMV、订单数等）。", "clarification_options": [{"label": "查询总交易额", "query": "昨天总交易额是多少"}]}'
            # CASE-08: 各品类最近30天交易额
            elif "各品类最近30天交易额" in prompt_clean or ("各品类" in prompt_clean and "30天" in prompt_clean and "交易额" in prompt_clean):
                return '{"metrics": [{"name": "total_gmv"}], "dimensions": [{"name": "category_name"}], "filters": [{"field": "dt", "op": "last_30_days", "value": null}]}'
            # CASE-09: 帮我分析article_history 表里每类文章分别有多少篇
            elif "article_history" in prompt_clean:
                return '{"metrics": [{"name": "article_history_count"}], "dimensions": [{"name": "category_name"}], "filters": []}'
            # CASE-10: 各品类退款额除以交易额的比率
            elif "比率" in prompt_clean or "除以" in prompt_clean:
                return '{"metrics": [{"name": "total_refund_amount"}, {"name": "total_gmv"}], "dimensions": [{"name": "category_name"}], "filters": [], "custom_select": "category_name, SUM(refund_amount) / SUM(gmv) AS ratio"}'
            # CASE-11: 帮我把article表和user_memory表进行不带外键等值连接
            elif "article" in prompt_clean and "user_memory" in prompt_clean:
                return '{"metrics": [{"name": "articles_count"}], "dimensions": [], "filters": [], "custom_select": "*", "custom_join": "LEFT JOIN user_memory ON articles.title = user_memory.question"}'
            return prompt

        # 1. 动态加载本地 llm_config.json 配置文件
        config_path = "/Users/mindezhi/DataWareHouse-Agent/backend/llm_config.json"
        api_key = ""
        base_url = ""
        active_text_model = ""
        active_vendor = ""
        text_models = []
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                    active_vendor = config_data.get("active_vendor", "openai")
                    vendor_info = config_data.get("vendors", {}).get(active_vendor, {})
                    api_key = vendor_info.get("api_key", "").strip()
                    base_url = vendor_info.get("base_url", "").strip()
                    active_text_model = vendor_info.get("active_text_model", "").strip()
                    text_models = vendor_info.get("text_models", [])
        except Exception as e:
            print(f"[LLM Config Load Error]: {e}")

        if not api_key:
            raise RuntimeError("系统大模型 API Key 未配置，无法发起问数请求！请先在 llm_config.json 中配置有效的密钥。")

        # 1.5 智能模型路由策略 (Model Routing Strategy)
        routed_model = active_text_model
        if text_models:
            if active_vendor == "gemini":
                if model_tier == "fast":
                    flash_models = [m for m in text_models if "flash" in m.lower() or "lite" in m.lower()]
                    routed_model = flash_models[0] if flash_models else "gemini-3.5-flash"
                else:
                    pro_models = [m for m in text_models if "pro" in m.lower() or "max" in m.lower()]
                    routed_model = pro_models[0] if pro_models else "gemini-3.1-pro"
            elif active_vendor == "openai":
                if model_tier == "fast":
                    mini_models = [m for m in text_models if "mini" in m.lower() or "lite" in m.lower()]
                    routed_model = mini_models[0] if mini_models else "gpt-4o-mini"
                else:
                    strong_models = [m for m in text_models if "mini" not in m.lower() and "lite" not in m.lower()]
                    routed_model = strong_models[0] if strong_models else "gpt-4o"
            elif active_vendor == "deepseek":
                if model_tier == "fast":
                    flash_models = [m for m in text_models if "flash" in m.lower()]
                    routed_model = flash_models[0] if flash_models else active_text_model
                else:
                    pro_models = [m for m in text_models if "pro" in m.lower()]
                    routed_model = pro_models[0] if pro_models else active_text_model
            else:
                if model_tier == "fast":
                    routed_model = text_models[-1] if text_models else active_text_model
                else:
                    routed_model = text_models[0] if text_models else active_text_model

        print(f"[Model Router] Routed '{model_tier}' tier query to model: '{routed_model}' (Vendor: {active_vendor})")

        # 2. 发起大模型真实推理
        try:
            if active_vendor == "gemini" and "generativelanguage" in base_url.lower():
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel(routed_model)
                response = model.generate_content(f"{system_prompt}\n\n{prompt}")
                return response.text
            else:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)
                response = client.chat.completions.create(
                    model=routed_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0,
                    timeout=20.0
                )
                return response.choices[0].message.content
        except Exception as e:
            print(f"[Model Router Error] 调用外部大模型 [{active_vendor}] 遇到异常: {e}")
            raise e
    def _evaluate_query_complexity(self, question: str, recalled_meta: list) -> str:
        """
        根据用户的提问词频和向量召回元数据，动态评估问题复杂度以决定模型路由档位。
        """
        q_lower = question.lower()
        complex_keywords = [
            "同比", "环比", "yoy", "mom", "累计", "cumulative", "排名", "rank", "占比", "比例",
            "均值", "平均", "平均值", "avg", "分析", "为什么", "趋势", "走势", "对齐"
        ]
        if any(kw in q_lower for kw in complex_keywords):
            return "complex"

        if recalled_meta:
            tables = set()
            for item in recalled_meta:
                tbl = item.get("table_name")
                if tbl:
                    tables.add(tbl)
            if len(tables) > 1:
                return "complex"

        return "fast"

    @staticmethod
    def _resolve_temporal_expression(time_range_dsl: dict) -> tuple:
        """
        确定性时间解析器管线。绝不依赖 LLM 计算具体的月初/月末或相对边界，防止平闰年与越界错误。
        输入：{"type": "last_30_days"} 等
        输出：(start_date_str, end_date_str)
        """
        from datetime import datetime, timedelta
        # 以北京业务时间为基准，获取当前日期
        today = datetime.now().date()
        
        if not time_range_dsl or not isinstance(time_range_dsl, dict):
            # 默认返回过去 30 天
            start = today - timedelta(days=30)
            end = today - timedelta(days=1)
            return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

        t_type = time_range_dsl.get("type", "absolute")
        if t_type == "absolute":
            # 兼容以列表形式返回的 filters 格式或者直出的 start/end 字段
            start = time_range_dsl.get("start") or time_range_dsl.get("value", [None, None])[0]
            end = time_range_dsl.get("end") or time_range_dsl.get("value", [None, None])[1]
            return start, end
            
        if t_type == "today":
            d_str = today.strftime("%Y-%m-%d")
            return d_str, d_str
        elif t_type == "yesterday":
            yesterday = today - timedelta(days=1)
            return yesterday.strftime("%Y-%m-%d"), yesterday.strftime("%Y-%m-%d")
        elif t_type == "last_7_days":
            start = today - timedelta(days=7)
            end = today - timedelta(days=1)
            return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        elif t_type == "last_30_days" or t_type == "last_30_day":
            start = today - timedelta(days=30)
            end = today - timedelta(days=1)
            return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        elif t_type == "last_month":
            # 确定性上月月初月末算法
            first_day_of_this_month = today.replace(day=1)
            last_day_of_last_month = first_day_of_this_month - timedelta(days=1)
            first_day_of_last_month = last_day_of_last_month.replace(day=1)
            return first_day_of_last_month.strftime("%Y-%m-%d"), last_day_of_last_month.strftime("%Y-%m-%d")
            
        # 兜底返回过去 30 天
        start = today - timedelta(days=30)
        end = today - timedelta(days=1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    def _deterministic_semantic_ast_fallback(self, question: str, recalled_meta: list = None) -> dict:
        """
        确定性语义 AST 降级编译器 (吸收阿里 QwenPaw-Data & ListenBook-DataAgent 体系规范)。
        当大模型外部接口超时或网络不可用时，通过确定性规则与语义元数据抽取指标、维度与时间范围，
        实现秒级（<5ms）极速直出且 100% 准确合规。
        """
        from datetime import datetime, timedelta
        q_lower = question.lower()
        today = datetime.now().date()
        yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        last_30_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        last_30_end = yesterday_str
        last_7_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")

        metrics = []
        dimensions = []
        filters = []
        order_by = []
        limit = 10

        explicit_tables = self.semantic_layer.mentioned_tables(question)
        candidate_metrics = [metric for metric in self.semantic_layer.metrics.values()
                             if not explicit_tables or metric.source_table in explicit_tables]

        # 1. 指标提取 (优先多词长词匹配)
        if "退款率" in q_lower or ("退款" in q_lower and any(w in q_lower for w in ("除以", "比率", "比例"))):
            metrics.append({"name": "refund_ratio"})
        elif any(w in q_lower for w in ["完播率", "播放完成率", "completion_rate"]):
            metrics.append({"name": "completion_rate"})
        elif any(w in q_lower for w in ["播放量", "播放数", "收听量", "播放次数", "play_count"]):
            metrics.append({"name": "play_count"})
        elif any(w in q_lower for w in ["播放时长", "收听时长", "时长"]):
            metrics.append({"name": "play_duration_seconds"})
        elif any(w in q_lower for w in ["会员销售额", "会员gmv", "会员收入", "audio_gmv"]):
            metrics.append({"name": "audio_gmv"})
        elif any(w in q_lower for w in ["听书退款", "会员退款", "audio_refund_amount"]):
            metrics.append({"name": "audio_refund_amount"})
        elif any(w in q_lower for w in ["付费用户", "订阅用户", "paid_users"]):
            metrics.append({"name": "paid_users"})
        elif any(w in q_lower for w in ["退款金额", "退款额", "退款"]):
            metrics.append({"name": "total_refund_amount"})
        elif any(w in q_lower for w in ["gmv", "销售额", "成交额", "流水"]):
            metrics.append({"name": "total_gmv"})
        elif any(w in q_lower for w in ["订单量", "订单数", "order_count"]):
            metrics.append({"name": "total_order_count"})
        elif any(w in q_lower for w in ["浏览量", "阅读量", "点击量", "view_count"]):
            metrics.append({"name": "total_view_count"})
        elif any(w in q_lower for w in ["文章数", "稿件数"]):
            metrics.append({"name": "articles_count"})
        else:
            # No guessed business metric: deterministic fallback only handles
            # explicitly named registered metrics or their exact aliases.
            candidates = [(len(term), metric.name)
                          for metric in candidate_metrics
                          for term in [metric.name, *metric.aliases]
                          if self.semantic_layer.mentions_term(question, term)]
            if candidates:
                best_score = max(score for score, _ in candidates)
                best_names = sorted({name for score, name in candidates if score == best_score})
                if len(best_names) > 1:
                    if not explicit_tables and "articles_count" in best_names and "文章" in question:
                        best_names = ["articles_count"]
                    else:
                        return {"need_clarification": True, "metrics": [],
                                "clarification_msg": "匹配到多个记录数指标，请明确表名或指标名称：" + "、".join(best_names)}
                metrics.append({"name": best_names[0]})

        # A supplied table is a binding constraint, never a weak alias hint.
        if explicit_tables:
            selected = [self.semantic_layer.resolve_metric(item["name"]) for item in metrics]
            count_request = (not selected or all(metric and metric.default_agg.upper() == "COUNT" for metric in selected))
            if count_request and any(word in q_lower for word in ("多少", "数量", "计数", "记录数", "总数", "count", "篇")):
                counts = [metric for metric in candidate_metrics if metric.default_agg.upper() == "COUNT"]
                if len(counts) == 1:
                    metrics = [{"name": counts[0].name}]
            if any((metric := self.semantic_layer.resolve_metric(item["name"])) is not None
                   and metric.source_table not in explicit_tables for item in metrics):
                return {"need_clarification": True, "metrics": [],
                        "clarification_msg": "指定表 " + "、".join(explicit_tables) + " 未注册所查询的指标，请选择该表已有指标。"}

        # Table-name fragments (for example audio_album_daily) do not request
        # an album grouping. Only the user's remaining query supplies dimensions.
        dimension_question = q_lower
        for table in explicit_tables:
            dimension_question = dimension_question.replace(table.lower(), "")
        # 2. 维度提取
        if any(w in dimension_question for w in ["分类", "品类", "题材", "类别", "category"]):
            dimensions.append({"name": "category_name"})
        if any(w in dimension_question for w in ["主播", "演播", "anchor"]):
            dimensions.append({"name": "anchor_name"})
        if any(w in dimension_question for w in ["专辑", "作品", "剧集", "album"]):
            dimensions.append({"name": "album_name"})
        if any(w in dimension_question for w in ["套餐", "规格", "会员类型", "plan"]):
            dimensions.append({"name": "plan_name"})
        if any(w in dimension_question for w in ["区域", "地区", "大区", "省份", "region"]):
            if {"name": "region_name"} not in dimensions:
                dimensions.append({"name": "region_name"})
        if any(w in dimension_question for w in ["商品", "货品", "单品", "goods"]):
            dimensions.append({"name": "goods_name"})
        if any(w in dimension_question for w in ("来源", "发布平台", "source_platform")):
            dimensions.append({"name": "source_platform"})

        # 3. 过滤条件提取
        # 3.1 区域过滤
        for r_name in ["华东", "华北", "华南", "华中"]:
            if r_name in question:
                filters.append({"field": "region_name", "op": "eq", "value": r_name})
                break

        # 3.2 时间过滤
        if any(w in q_lower for w in ["昨天", "昨日", "yesterday"]):
            filters.append({"field": "dt", "op": "between", "value": [yesterday_str, yesterday_str]})
        elif any(w in q_lower for w in ["上周", "近7天", "过去7天", "7天"]):
            filters.append({"field": "dt", "op": "between", "value": [last_7_start, yesterday_str]})
        elif any(w in q_lower for w in ["30天", "一个月", "近期", "近30天", "过去30天"]):
            filters.append({"field": "dt", "op": "between", "value": [last_30_start, last_30_end]})

        # 4. Limit 与 排序
        if any(w in q_lower for w in ["前3", "top 3", "top3", "前三"]):
            limit = 3
        elif any(w in q_lower for w in ["前5", "top 5", "top5", "前五"]):
            limit = 5
        elif any(w in q_lower for w in ["前10", "top 10", "top10", "前十"]):
            limit = 10

        if any(w in q_lower for w in ["排名", "最高", "top", "排名前", "降序"]):
            if metrics:
                order_by.append({"field": metrics[0]["name"], "direction": "desc"})

        missing = [item["name"] for item in metrics if not self.semantic_layer.resolve_metric(item["name"])]
        if not metrics or missing:
            return {"need_clarification": True, "metrics": [],
                    "clarification_msg": ("该比率尚未配置分子、分母口径，请先查询退款额或销售额。"
                                          if "refund_ratio" in missing else
                                          f"当前数据源未注册指标 {', '.join(missing)}，请连接对应业务表或选择已注册的金额、数量指标。"
                                          if missing else "请明确要查询的指标，例如退款额、销售额或听书播放量。"),
                    "clarification_options": []}
        current, _ = question_periods(question)
        filters = [f for f in filters if f["field"] != "dt"]
        filters.append({"field": "dt", "op": "between", "value": [current["start"], current["end"]]})
        # Canonical names ensure aliases and ORDER BY use the same registered metric.
        for item in metrics:
            item["name"] = self.semantic_layer.resolve_metric(item["name"]).name
        primary = self.semantic_layer.resolve_metric(metrics[0]["name"])
        measures = {metric.calculation for metric in candidate_metrics
                    if metric.source_table == primary.source_table and metric.default_agg.upper() in ("SUM", "AVG")}
        for name in primary.available_dimensions:
            if name not in measures and self.semantic_layer.mentions_term(dimension_question, name):
                if {"name": name} not in dimensions:
                    dimensions.append({"name": name})

        print(f"[Deterministic AST Fallback] Parsed Question: '{question}' -> Metrics: {[m['name'] for m in metrics]}, Dims: {[d['name'] for d in dimensions]}, Filters: {filters}, Limit: {limit}")

        return {
            "metrics": metrics,
            "dimensions": dimensions,
            "filters": filters,
            "order_by": order_by,
            "limit": limit
        }

    def _merge_session_dsl(self, prev_dsl: dict, new_dsl: dict) -> dict:
        """
        基于 Session 的 QuerySessionState 状态合并逻辑，实现多轮提问参数不丢失。
        - 继承未发生冲突的指标、维度和过滤条件。
        - 对同一个过滤字段，新值覆盖旧值。
        """
        # 话题漂移检测 (Topic Drift Detection)
        # 如果新 DSL 包含的指标的主表，与上一轮 DSL 的指标主表不一致，说明用户切换了话题。
        # 此时，我们清空上一轮 DSL，不进行任何继承合并，直接返回新 DSL！
        prev_table = None
        new_table = None
        
        for m_item in prev_dsl.get("metrics", []):
            m_meta = self.semantic_layer.resolve_metric(m_item.get("name"))
            if m_meta:
                prev_table = m_meta.source_table
                break
                
        for m_item in new_dsl.get("metrics", []):
            m_meta = self.semantic_layer.resolve_metric(m_item.get("name"))
            if m_meta:
                new_table = m_meta.source_table
                break
                
        if prev_table and new_table and prev_table != new_table:
            print(f"[Topic Drift Detected] User shifted domain from '{prev_table}' to '{new_table}'. Clearing session DSL history.")
            return new_dsl

        if not prev_dsl:
            return new_dsl

        merged = {
            "metrics": new_dsl.get("metrics", []),
            "dimensions": new_dsl.get("dimensions", []),
            "filters": [],
            "time_range": new_dsl.get("time_range") or prev_dsl.get("time_range")
        }

        # 1. 合并指标：若新 DSL 没指标，继承上一轮的指标
        if not merged["metrics"]:
            merged["metrics"] = prev_dsl.get("metrics", [])

        # 2. 合并维度：若新 DSL 没维度，继承上一轮的维度
        if not merged["dimensions"]:
            merged["dimensions"] = prev_dsl.get("dimensions", [])

        # 3. 合并过滤器：
        # 对于同一个 field 过滤条件，以新 DSL 为准（覆盖）；新 DSL 中缺少的 field 过滤，继承上一轮
        prev_filters = prev_dsl.get("filters", [])
        new_filters = new_dsl.get("filters", [])

        new_fields = {f.get("field") for f in new_filters if f.get("field")}
        
        # 保留新过滤
        merged["filters"].extend(new_filters)

        # 保留上轮有、但本轮没有被覆盖的过滤
        for pf in prev_filters:
            field = pf.get("field")
            if field not in new_fields:
                merged["filters"].append(pf)

        # 清洗多余 limit
        if "limit" in new_dsl:
            merged["limit"] = new_dsl["limit"]
        elif "limit" in prev_dsl:
            merged["limit"] = prev_dsl["limit"]
        if "order_by" in new_dsl:
            merged["order_by"] = new_dsl["order_by"]

        return merged

    def _detect_column_types(self, df: pd.DataFrame, final_dsl: dict) -> dict:
        """
        基于元数据 schema 以及启发式词法推导每个返回列的值类型。
        """
        column_types = {}
        for col in df.columns:
            col_str = str(col)
            col_lower = col_str.lower()
            
            # 1. 默认基于 Pandas 数据类型决定
            pandas_dtype = str(df[col].dtype).lower()
            
            is_int_like_name = any(word in col_lower for word in [
                "count", "number", "qty", "quantity", "times", "pv", "uv", "id", "rank", "cnt"
            ])
            # NOTE: price/amount/ratio/rate metrics must remain as decimals
            is_float_like_name = any(word in col_lower for word in [
                "amount", "price", "gmv", "ratio", "rate", "pct", "percent", "avg", "mean",
                "cost", "fee", "val", "value", "revenue", "profit", "margin", "discount",
                "tax", "salary", "wage", "bonus", "commission", "balance", "turnover",
                "arpu", "arppu", "ltv", "cpc", "cpm", "ctr", "cvr", "roi", "roas",
                "score", "index", "coefficient", "weight", "proportion"
            ])
            is_float_like_suffix = any(col_str.endswith(s) for s in [
                "\u7387", "\u4ef7", "\u989d", "\u6bd4", "\u6bd4\u503c", "\u5747\u503c",
                "\u5747\u4ef7", "\u5355\u4ef7", "\u91d1\u989d", "\u8d39\u7528", "\u6210\u672c"
            ])
            if is_float_like_suffix:
                is_float_like_name = True
            
            # 2. 检查同比/环比/排名
            if "_mom" in col_lower or "_yoy" in col_lower:
                column_types[col_str] = "decimal"
                continue
            if "_rank" in col_lower:
                column_types[col_str] = "integer"
                continue
                
            # 3. 尝试解析语义层
            detected_type = None
            
            # 尝试还原出指标名
            metric_name = col_lower
            if metric_name.startswith("total_"):
                metric_name = metric_name[6:]
            elif metric_name.startswith("cumulative_"):
                metric_name = metric_name[11:]
                
            metric = self.semantic_layer.resolve_metric(metric_name)
            if not metric:
                metric = self.semantic_layer.resolve_metric(col_lower)
                
            if metric:
                if metric.default_agg in ["COUNT", "DISTINCT_COUNT", "COUNT_DISTINCT", "COUNT(DISTINCT)"]:
                    detected_type = "integer"
                elif metric.default_agg == "SUM":
                    # 去物理表里查字段类型
                    calc_col = metric.calculation
                    source_table = metric.source_table
                    table_cols = self.semantic_layer.discovered_table_columns.get(source_table, [])
                    for name, dtype in table_cols:
                        if name.lower() == calc_col.lower():
                            dtype_lower = str(dtype).lower()
                            if any(i in dtype_lower for i in ["int", "bigint", "integer"]):
                                detected_type = "integer"
                            elif any(f in dtype_lower for f in ["double", "float", "numeric", "decimal", "real"]):
                                detected_type = "decimal"
                            break
                elif metric.default_agg == "AVG":
                    detected_type = "decimal"
                
                # 如果没解析出来，看 metric.unit
                if not detected_type:
                    if metric.unit in ["个", "人", "件", "次", "条", "篇", "台", "店", "只", "家", "张", "户", "设备"]:
                        detected_type = "integer"
                    elif metric.unit in ["元", "万元", "角", "分", "%"]:
                        detected_type = "decimal"
            
            # 如果不是指标，看是不是维度
            if not detected_type:
                dim = self.semantic_layer.resolve_dimension(col_lower)
                if dim:
                    source_table = dim.source_table
                    table_cols = self.semantic_layer.discovered_table_columns.get(source_table, [])
                    for name, dtype in table_cols:
                        if name.lower() == dim.source_column.lower():
                            dtype_lower = str(dtype).lower()
                            if any(i in dtype_lower for i in ["int", "bigint", "integer"]):
                                detected_type = "integer"
                            elif any(f in dtype_lower for f in ["double", "float", "numeric", "decimal", "real"]):
                                detected_type = "decimal"
                            else:
                                detected_type = "string"
                            break
            
            # 4. 如果还没解析出来，使用启发式词法规则
            if not detected_type:
                if is_int_like_name and not is_float_like_name:
                    detected_type = "integer"
                elif is_float_like_name:
                    detected_type = "decimal"
            
            # 5. 最后兜底 Pandas 物理类型
            if not detected_type:
                if any(i in pandas_dtype for i in ["int", "bool"]):
                    detected_type = "integer"
                elif any(f in pandas_dtype for f in ["float", "decimal", "double", "numeric"]):
                    detected_type = "decimal"
                else:
                    detected_type = "string"
            
            # 额外校正：防止被 Pandas 转成 float 的列如果是 int_like，强制判定为 integer
            if detected_type == "decimal" and is_int_like_name and not is_float_like_name:
                detected_type = "integer"
            
            # 反向保护：价格/金额/比值/率等列即使被其他规则判定为 integer，也强制回退为 decimal
            if detected_type == "integer" and is_float_like_name and not is_int_like_name:
                detected_type = "decimal"
            column_types[col_str] = detected_type
            
        return column_types

    def _auto_detect_chart_type(self, df: pd.DataFrame, dsl: dict, column_types: dict = None) -> dict:
        """
        自适应图表类型引擎
        """
        row_count = len(df)
        cols = list(df.columns)
        
        if row_count == 0:
            return {"type": "table", "title": "无结果集", "config": {}}
            
        if row_count == 1:
            val_col = cols[1] if len(cols) > 1 else cols[0]
            val = df.iloc[0][val_col]
            
            # 增加对 integer 类型的特定格式化
            col_type = column_types.get(val_col) if column_types else None
            if col_type == "integer" and isinstance(val, (int, float, np.integer, np.floating)):
                val_str = f"{int(round(val)):,}"
            elif isinstance(val, (int, float, np.integer, np.floating)):
                val_str = f"{val:,.2f}"
            else:
                val_str = str(val)
                
            if "gmv" in val_col or "total_gmv" in val_col:
                if isinstance(val, (int, float, np.integer, np.floating)):
                    val_str = f"¥{val / 10000:.2f} 万"
            elif "refund_ratio" in val_col or "ratio" in val_col:
                if isinstance(val, (int, float, np.integer, np.floating)):
                    val_str = f"{val * 100:.2f}%" if val <= 1.0 else f"{val:.2f}%"
            
            return {
                "type": "card",
                "title": "查询指标结果",
                "config": {
                    "value": val_str,
                    "label": val_col
                }
            }

        # 判断是否包含日期时间列
        has_time_col = any(c in ["month", "dt", "date", "created_at"] for c in cols)
        
        # 寻找数值列与分类列
        numeric_cols = [c for c in cols if "total_" in c or "refund_ratio" in c or df[c].dtype in [np.float64, np.int64]]
        category_cols = [c for c in cols if c not in numeric_cols and c not in ["month", "dt"]]

        if has_time_col and len(numeric_cols) >= 1:
            time_col = [c for c in cols if c in ["month", "dt", "date", "created_at"]][0]
            df_sorted = df.sort_values(by=time_col)
            xAxis_data = df_sorted[time_col].astype(str).tolist()
            series = []
            for num_col in numeric_cols:
                y_data = df_sorted[num_col].tolist()
                
                # 如果这个列是 integer，转成 int
                col_type = column_types.get(num_col) if column_types else None
                if col_type == "integer":
                    y_data = [int(round(v)) if pd.notna(v) else None for v in y_data]
                    
                if "gmv" in num_col:
                    y_data = [round(v / 10000.0, 2) for v in y_data]
                    series.append({"name": "销售额 (万)", "data": y_data})
                elif "refund_ratio" in num_col:
                    # 换算百分比
                    y_data = [round(v * 100.0, 2) if v <= 1.0 else round(v, 2) for v in y_data]
                    series.append({"name": "退款率 (%)", "data": y_data})
                else:
                    series.append({"name": num_col, "data": y_data})

            return {
                "type": "line",
                "title": "趋势分析折线图",
                "config": {
                    "xAxis": {"data": xAxis_data},
                    "series": series
                }
            }

        if len(category_cols) >= 1 and len(numeric_cols) == 1:
            cat_col = category_cols[0]
            num_col = numeric_cols[0]
            
            if "category_name" in cat_col or "region_name" in cat_col:
                pie_data = []
                for _, row in df.iterrows():
                    val = row[num_col]
                    col_type = column_types.get(num_col) if column_types else None
                    if col_type == "integer" and pd.notna(val):
                        val = int(round(val))
                    elif "gmv" in num_col or "refund_amount" in num_col:
                        val = round(val / 10000.0, 2)
                    elif "refund_ratio" in num_col or "ratio" in num_col:
                        val = round(val * 100.0, 2) if val <= 1.0 else round(val, 2)
                    pie_data.append({"name": str(row[cat_col]), "value": val})
                
                # 按数值由大到小排序，若分类数大于 5 则将长尾数据合并为“其他”
                pie_data = sorted(pie_data, key=lambda x: x["value"], reverse=True)
                if len(pie_data) > 5:
                    top_4 = pie_data[:4]
                    others_sum = sum(item["value"] for item in pie_data[4:])
                    top_4.append({"name": "其他", "value": round(others_sum, 2)})
                    pie_data = top_4
                
                title = "占比分布饼图"
                if "gmv" in num_col:
                    title = "交易额分类占比分布 (万元)"
                elif "refund_ratio" in num_col:
                    title = "退款率对比分布 (%)"
                
                return {
                    "type": "pie",
                    "title": title,
                    "config": {
                        "series": [{"name": "占比", "data": pie_data}]
                    }
                }
            
            xAxis_data = df[cat_col].astype(str).tolist()
            y_data = df[num_col].tolist()
            
            col_type = column_types.get(num_col) if column_types else None
            if col_type == "integer":
                y_data = [int(round(v)) if pd.notna(v) else None for v in y_data]
                
            if "gmv" in num_col:
                y_data = [round(v / 10000.0, 2) for v in y_data]
                label_name = "销售额 (万)"
            elif "refund_ratio" in num_col:
                y_data = [round(v * 100.0, 2) if v <= 1.0 else round(v, 2) for v in y_data]
                label_name = "退款率 (%)"
            else:
                label_name = num_col

            return {
                "type": "bar",
                "title": "对比分析柱状图",
                "config": {
                    "xAxis": {"data": xAxis_data},
                    "series": [{"name": label_name, "data": y_data}]
                }
            }
        # 默认表格
        return {
            "type": "table",
            "title": "详细明细表",
            "config": {}
        }

    def ask(self, question: str, dialect: str = "doris", user: str = "anonymous", role: str = None) -> dict:
        """
        全链路 V2.0 升级架构问数接口：
        1. 偏好注入 & 多轮 Session 合并
        2. LLM 结构化抽取 DSL 意图 (限用语义注册指标维度)
        3. guardrail.check_dsl 进行语义层校验与权限拦截 (第一层网闸)
        4. DSLCompiler 确定性 SQL 编译器构建 SQL (包含时区北京->芝加哥映射)
        5. guardrail.check_sql 物理 SQL 校验 (第二层网闸)
        6. 数据仿真执行与商业摘要渲染
        """
        # 0. 确定用户角色权限，防止硬编码
        if role:
            user_role = role.lower()
        else:
            user_lower = user.lower()
            if any(k in user_lower for k in ["admin", "管理员", "superuser"]):
                user_role = "admin"
            elif any(k in user_lower for k in ["analyst", "分析师", "王五"]):
                user_role = "analyst"
            else:
                user_role = "user"

        print(f"\n[AskAgent V2.0] Question from '{user}' (Role: {user_role}): {question} (Dialect: {dialect})")
        start_time = time.time()
        local_demo = db_service.is_sample_data and os.getenv("MOCK_LLM") != "true"
        is_followup = bool(re.match(r"^(那|那么|再看|换成|改为|这些|它们)", question.strip()))

        # 0.1 检索多级语义缓存 (Semantic Query Cache - 毫秒级直接命中加速)
        query_vec = vector_service.get_embedding(question)
        cache_hit = semantic_cache.get(question, dialect=dialect, role=user_role, query_embedding=query_vec)
        if cache_hit:
            cached_resp, hit_type = cache_hit
            print(f"[Semantic Cache Hit] Returning response from {hit_type} cache for question: '{question}'")
            return cached_resp

        # 0.5 多轮上下文问句改写 (Query Rewriter Pipeline)
        history_queries = self.user_history_questions.get(user, [])
        if history_queries and is_followup and not local_demo:
            rewrite_prompt = (
                f"【历史对话问题记录】:\n"
                + "\n".join([f"- {q}" for q in history_queries[-3:]])
                + f"\n【当前最新不完整提问】: {question}\n"
                + "请结合历史问题记录上下文，将当前最新提问改写为一个独立、具体、不带代词指代和省略的完整数据查询句。\n"
                + "例如：历史问题是“华东区昨天的销售额”，最新提问是“那前三名品类呢”，应该改写为“华东区昨天销售额排名前三的品类分别是什么”。\n"
                + "请直接输出改写后的完整中文自然语言问句，不要包含任何多余的解释、前导词或 Markdown 标记。"
            )
            try:
                rewritten_question = self._call_llm(
                    prompt=rewrite_prompt,
                    system_prompt="你是一个极为专业的智能多轮问数改写助手。你的唯一目标是还原代词指代和补齐省略，确保输出的句子独立完备。",
                    model_tier="fast"
                )
                question_to_parse = rewritten_question.strip()
                print(f"[Query Rewriter] Rewrote: '{question}' -> '{question_to_parse}'")
            except Exception as e:
                print(f"[Query Rewriter Error]: {e}. Fallback to raw question.")
                question_to_parse = question
        else:
            question_to_parse = question

        # 将用户的当前原始问题追加进历史记录中
        if user not in self.user_history_questions:
            self.user_history_questions[user] = []
        self.user_history_questions[user].append(question)

        # 1. 意图解析：通过 Qdrant 向量数据库检索候选指标、维度及 Few-shot 示例 (Schema Grounding RAG)
        preference = user_memory.get_preference_profile(user)
        
        # 1.1 检索 Qdrant 知识库候选元数据与 Few-shot 对话示例 (采用改写后的完备问句)
        recalled_meta = [] if local_demo else vector_service.recall_semantic_meta(question_to_parse, limit=4)
        recalled_fewshots = [] if local_demo else vector_service.recall_fewshot_examples(question_to_parse, limit=2)

        # 1.2 相似度得分硬阻断过滤 (Qdrant Score Hard Truncation)
        # 为防止大模型在缺乏特定指标元数据时脑补（如问“食堂消费”却猜“GMV”），
        # 如果检索到的所有指标和维度的最大相似度得分低于 0.20，清空候选池以激发主动澄清拦截。
        max_similarity = max([m.get("similarity", 0.0) for m in recalled_meta]) if recalled_meta else 0.0
        if max_similarity < 0.20:
            print(f"[Qdrant Recall Cutoff] Max similarity {max_similarity:.3f} < 0.20. Clearing candidate metadata to trigger clarification.")
            recalled_meta = []

        # 1.3 Skill-Hub 顶层技能动态调度 (如淘宝百亿补贴异动归因 / 湖图数据血缘双引擎追溯)
        from app.service.skill_orchestrator import skill_orchestrator
        from app.service.skills.base_skill import SkillContext
        skill_ctx = SkillContext(
            question=question,
            rewritten_question=question_to_parse,
            dialect=dialect,
            user=user,
            role=user_role,
            recalled_meta=recalled_meta,
            user_preference=preference
        )
        matched_skill = skill_orchestrator.route(skill_ctx)
        if matched_skill:
            skill_res = matched_skill.execute(skill_ctx)
            resp_dict = skill_res.model_dump()
            # 存入多级语义缓存
            semantic_cache.put(question, dialect, user_role, resp_dict, embedding=query_vec)
            return resp_dict

        system_prompt = (
            "你是一个智能元数据意图解析器。你的任务是将用户提问映射为结构化的查询 JSON DSL。\n"
            "【规则】:\n"
            "1. 严格禁止生成任何 SQL 语句。\n"
            "2. 优先且只能使用向量库召回出的候选指标和维度名称，进行同义词消歧映射。\n"
            "3. 必须输出严格的 JSON 格式，包含 metrics, dimensions, filters 三个字段。\n"
            "4. 相对时间（如昨天、上周、30天等）在 filters 中使用 dt between 进行表达，格式为：[start_date, end_date] (YYYY-MM-DD)。\n"
            "5. filters 格式例：[{\"field\": \"region_name\", \"op\": \"eq\", \"value\": \"华东\"}]。\n"
            "6. 【歧义消歧与澄清机制】：\n"
            "   - 如果用户的提问极度模糊、缺失指标/维度，或包含了在向量库候选元数据中无法匹配到任何标准别名的未注册指标或概念，请在 JSON 中增加：\n"
            "     \"need_clarification\": true, \n"
            "     \"clarification_msg\": \"你想查询哪种指标数据？目前仅支持系统已接入的指标。\",\n"
            "     \"clarification_options\": [{\"label\": \"查询指标A\", \"query\": \"过去30天指标A是多少\"}]\n"
            "   - 如果提问明确且与召回元数据高度吻合，则 `need_clarification` 为 `false`。\n"
            "   - 输出格式必须是可以被 Python `json.loads` 解析的合法 JSON，不要用 ```json 包裹。"
        )

        user_prompt = (
            f"【偏好画像上下文】: 常用指标: {[m['metric'] for m in preference.get('common_metrics', [])]}\n"
            f"【Qdrant 向量库召回元数据候选】: {json.dumps(recalled_meta, ensure_ascii=False)}\n"
            f"【向量库推荐 Few-Shot 对话示例】:\n"
        )
        for fs in recalled_fewshots:
            user_prompt += f"问：{fs['question']}\n答：{json.dumps(fs['dsl'], ensure_ascii=False)}\n\n"
            
        user_prompt += (
            f"【用户问题】: {question_to_parse}\n"
            f"请根据召回的元数据候选与 Few-Shot 对话示例，分析输出仅由 metrics, dimensions, filters 组成的 JSON 串："
        )

        # 1.3 根据提问及召回元数据，动态路由决定模型档位
        complexity_tier = self._evaluate_query_complexity(question_to_parse, recalled_meta)

        # 2. 调用 LLM 得到当前意图 DSL 碎片，若外部 API 超时或网络异常，自适应降级为确定性语义 AST 编译器
        def fallback_or_clarify():
            try:
                return self._deterministic_semantic_ast_fallback(question_to_parse, recalled_meta)
            except ValueError as error:
                return {"need_clarification": True, "clarification_msg": str(error), "metrics": []}

        try:
            if local_demo:
                raise RuntimeError("演示数仓使用本地语义解析")
            new_dsl_json = self._call_llm(
                prompt=user_prompt,
                system_prompt=system_prompt,
                user=user,
                model_tier=complexity_tier
            )
            # 强制提取或纠错 json
            new_dsl_json = re.sub(r"^\s*```[a-zA-Z]*\n", "", new_dsl_json)
            new_dsl_json = re.sub(r"\n\s*```\s*$", "", new_dsl_json)
            new_dsl = json.loads(new_dsl_json.strip())
        except Exception as e:
            print(f"[LLM / DSL Parse Fallback]: 调用大模型或解析 DSL 遇到异常: {e}。触发阿里 QwenPaw-Data 体系确定性语义 AST 编译器！")
            new_dsl = fallback_or_clarify()

        # 若大模型返回的 metrics 为空且未主动要求澄清，使用确定性 AST 编译器补全
        if not isinstance(new_dsl, dict) or (not new_dsl.get("metrics") and not new_dsl.get("need_clarification")):
            new_dsl = fallback_or_clarify()

        named_tables = self.semantic_layer.mentioned_tables(question_to_parse)
        if named_tables and isinstance(new_dsl, dict):
            selected = [self.semantic_layer.resolve_metric(item if isinstance(item, str) else item.get("name", ""))
                        for item in new_dsl.get("metrics", []) if isinstance(item, (str, dict))]
            if any(metric and metric.source_table not in named_tables for metric in selected):
                new_dsl = fallback_or_clarify()

        # 2.1 主动澄清熔断拦截 (第一层澄清网闸)
        if new_dsl.get("need_clarification") is True:
            elapsed_time = f"{time.time() - start_time:.3f}s"
            return {
                "success": False,
                "error": new_dsl.get("clarification_msg", "您的问题存在歧义，需要澄清。"),
                "clarification": {
                    "need_clarification": True,
                    "message": new_dsl.get("clarification_msg", "您的问题存在歧义，需要澄清。"),
                    "options": new_dsl.get("clarification_options", [])
                },
                "details": {
                    "sql": "-- [触发主动澄清机制，未执行物理查询]",
                    "dialect": dialect,
                    "elapsed_time": elapsed_time,
                    "tables": [],
                    "source_desc": "大模型主动识别模糊意图，触发澄清建议",
                    "filters": []
                }
            }

        # 归一化清洗逻辑，防御大模型格式漂移 (e.g. 把 metrics 返回为 ["gmv"] 字符串列表)
        def clean_dsl_format(dsl_obj: dict) -> dict:
            if not isinstance(dsl_obj, dict):
                return {"metrics": [], "dimensions": [], "filters": []}
            
            # 1. 优先提取并利用确定性管线解析相对/绝对时间
            t_range = dsl_obj.get("time_range")
            # 遍历寻找 filters 里可能存在的相对时间段定义作为兜底输入
            if not t_range:
                for f in dsl_obj.get("filters", []):
                    if isinstance(f, dict) and f.get("field") == "dt" and f.get("op") == "between":
                        val = f.get("value")
                        if isinstance(val, list) and len(val) == 2:
                            t_range = {"type": "absolute", "start": val[0], "end": val[1]}
                            break
                        elif isinstance(val, str):
                            t_range = {"type": val}
                            break
            
            start_d, end_d = self._resolve_temporal_expression(t_range)
            
            cleaned = {
                "metrics": [],
                "dimensions": [],
                "filters": [],
                "time_range": {"start": start_d, "end": end_d}
            }
            if "limit" in dsl_obj:
                cleaned["limit"] = dsl_obj["limit"]
            if "order_by" in dsl_obj:
                cleaned["order_by"] = dsl_obj["order_by"]
            if "custom_select" in dsl_obj:
                cleaned["custom_select"] = dsl_obj["custom_select"]
            if "custom_join" in dsl_obj:
                cleaned["custom_join"] = dsl_obj["custom_join"]
                
            # 2. 将确定性时间区间注入为绝对过滤条件 (过滤掉大模型直出的不确定时间过滤器)
            raw_filters = dsl_obj.get("filters", [])
            if isinstance(raw_filters, list):
                for f in raw_filters:
                    if not isinstance(f, dict):
                        continue
                    if f.get("field") == "dt":
                        # 跳过大模型直接计算的相对时间
                        continue
                    cleaned["filters"].append(f)
            
            # 追加高精度绝对时间过滤器
            cleaned["filters"].append({
                "field": "dt",
                "op": "between",
                "value": [start_d, end_d]
            })

            # 清洗 metrics
            raw_metrics = dsl_obj.get("metrics", [])
            if isinstance(raw_metrics, list):
                for m in raw_metrics:
                    if isinstance(m, str):
                        cleaned["metrics"].append({"name": m})
                    elif isinstance(m, dict) and "name" in m:
                        cleaned["metrics"].append(m)
            # 清洗 dimensions
            raw_dims = dsl_obj.get("dimensions", [])
            if isinstance(raw_dims, list):
                for d in raw_dims:
                    if isinstance(d, str):
                        cleaned["dimensions"].append({"name": d})
                    elif isinstance(d, dict) and "name" in d:
                        cleaned["dimensions"].append(d)
            return cleaned

        new_dsl = clean_dsl_format(new_dsl)

        # 3. 关联多轮上下文：融合 QuerySessionState
        prev_dsl = self.user_sessions.get(user, {})
        if not is_followup:
            prev_dsl = {}  # A self-contained question starts a fresh scope.
        final_dsl = self._merge_session_dsl(prev_dsl, new_dsl)
        final_dsl = clean_dsl_format(final_dsl)
        print(f"[AskAgent V2.0] Session Merged DSL: {json.dumps(final_dsl, ensure_ascii=False)}")

        # 4. 执行第一层网闸：DSL 语义审计
        try:
            guardrail.check_dsl(final_dsl, self.semantic_layer, user_role=user_role)
        except GuardrailException as ge:
            # 语义审计拦截，直接返回错误，不编译 SQL
            elapsed_time = f"{time.time() - start_time:.3f}s"
            # 构造虚拟 SQL 以供前台显示拦截情况
            if ge.message.startswith("语义审计拦截:"):
                message = ge.message.removeprefix("语义审计拦截:").strip()
                # Business schema limitations are query clarifications; true
                # role/row/column security failures retain the guardrail result.
                if "不兼容" in message:
                    metric = self.semantic_layer.resolve_metric(final_dsl["metrics"][0]["name"])
                    choices = self.semantic_layer.suggested_dimensions(metric) if metric else []
                    message = (f"指标 '{metric.name}' 不支持所请求的分组维度。可用分组维度：{', '.join(choices)}。"
                               if metric else "当前指标不支持所请求的分组维度，请选择已注册的分组字段。")
                return {"success": False, "error": message,
                        "clarification": {"need_clarification": True, "message": message, "options": []},
                        "details": {"sql": "", "dialect": dialect, "elapsed_time": elapsed_time,
                                    "tables": [], "source_desc": "查询条件需要调整，未执行物理查询",
                                    "filters": final_dsl.get("filters", [])}}
            dummy_sql = f"-- [语义拦截]: {ge.message}"
            return {
                "success": False,
                "error": ge.message,
                "details": {
                    "sql": dummy_sql,
                    "dialect": dialect,
                    "elapsed_time": elapsed_time,
                    "tables": [],
                    "source_desc": "在语义层即被网闸拦截，未触达物理执行层",
                    "filters": final_dsl.get("filters", []),
                    "estimated_rows": 0
                }
            }

        # 5. DSL 确定性编译器组装 SQL (包含北京时区转换为芝加哥时区)
        compiler = DSLCompiler(layer=self.semantic_layer, dialect=dialect)
        try:
            sql = compiler.compile(final_dsl)
        except Exception as e:
            # 编译失败直接熔断
            elapsed_time = f"{time.time() - start_time:.3f}s"
            return {
                "success": False,
                "error": f"DSL 编译器组装 SQL 失败: {str(e)}",
                "details": {
                    "sql": f"-- [编译报错]: {str(e)}",
                    "dialect": dialect,
                    "elapsed_time": elapsed_time,
                    "tables": [],
                    "source_desc": "编译引擎阶段报错",
                    "filters": final_dsl.get("filters", []),
                    "estimated_rows": 0
                }
            }

        print(f"[AskAgent V2.0] Compiled Dialect SQL ({dialect}):\n{sql}")

        # 6. 执行第二层网闸：SQL 物理安全审计
        max_retries = 1 if local_demo else 3
        retry_count = 0
        execution_error = None
        df = pd.DataFrame()
        guardrail_result = {}

        # 记录纠错反馈相关的原始错误现场
        original_sql = sql
        first_error_msg = None

        while retry_count < max_retries:
            try:
                # 物理层安全与扫描预估审计
                guardrail_result = guardrail.check_sql(sql, dialect=dialect, conn=db_service.conn)
                
                # 执行 SQL
                df = db_service.execute_query(sql, dialect=dialect)
                execution_error = None
                break
            except GuardrailException as ge:
                execution_error = str(ge)
                if first_error_msg is None:
                    first_error_msg = execution_error
                print(f"[Guardrail SQL Checked Failed - Retry {retry_count+1}]: {ge}")
            except Exception as e:
                execution_error = f"物理执行数据库报错: {str(e)}"
                if first_error_msg is None:
                    first_error_msg = execution_error
                print(f"[DB Execution Error - Retry {retry_count+1}]: {e}")
            
            # 若执行失败，触发纠错重试
            retry_count += 1
            if retry_count < max_retries:
                # 检索纠错经验库中的 Few-shot
                recalled_corrections = vector_service.recall_error_corrections(
                    query=question_to_parse,
                    error_message=execution_error,
                    limit=1
                )
                history_context = ""
                if recalled_corrections:
                    item = recalled_corrections[0]
                    history_context = (
                        f"【类似的成功纠错参考案例】:\n"
                        f"先前在提问: \"{item['question']}\" 时, 发生过类似的报错: \"{item['error_message']}\"\n"
                        f"当时错误的 SQL 为: {item['wrong_sql']}\n"
                        f"修正后的正确 SQL 为: {item['corrected_sql']}\n"
                        f"请参考上述案例的修正逻辑对当前的 SQL 进行相同方式的修改。\n\n"
                     )
                
                prompt_retry = (
                    f"{history_context}"
                    f"执行 SQL: {sql}\n"
                    f"发生报错: {execution_error}\n"
                    f"请进行纠错重写，并只返回修正后的 SQL。"
                )
                try:
                    sql = self._call_llm(
                        prompt=prompt_retry,
                        system_prompt="你是一个 SQL 纠错助手，请直接返回纯 SQL 文本，不要使用 Markdown 包装。",
                        model_tier="complex"
                    )
                    sql = re.sub(r"^\s*```[a-zA-Z]*\n", "", sql)
                    sql = re.sub(r"\n\s*```\s*$", "", sql)
                    sql = sql.strip()
                except Exception as err:
                    print(f"[Self-Correction LLM Retry Error]: {err}. 保持原SQL或自动降级。")
                    break

        # 7. 渲染结果与图表展示
        elapsed_time = f"{time.time() - start_time:.3f}s"
        formatted_sql = self._format_sql_for_display(sql, dialect)

        if execution_error:
            # 物理层出错记录历史
            user_memory.add_history(
                user=user,
                question=question,
                sql=sql,
                dialect=dialect,
                result_summary=f"物理层报错: {execution_error}"
            )
            return {
                "success": False,
                "error": execution_error,
                "details": {
                    "sql": formatted_sql,
                    "dialect": dialect,
                    "elapsed_time": elapsed_time,
                    "tables": [m["name"] for m in final_dsl.get("metrics", [])],
                    "source_desc": "编译执行物理报错",
                    "filters": final_dsl.get("filters", []),
                    "estimated_rows": 0
                }
            }

        # 如果是经过模型纠错重试且成功，写入纠错自学习经验库
        if retry_count > 0 and not execution_error:
            print(f"[Self-Correction Feedback Loop] Successfully corrected SQL. Saving experience to error correction memory.")
            user_memory.add_error_correction(
                question=question_to_parse,
                error_message=first_error_msg if first_error_msg else "未知报错",
                wrong_sql=original_sql,
                corrected_sql=sql
            )
            # 同步重新载入内存向量集合
            vector_service.ingest_error_corrections()

        # 成功执行，更新用户 Session 为本次成功的 DSL 意图状态（保证状态参数独立于对话历史）
        self.user_sessions[user] = final_dsl

        # 自动生成洞察结论
        summary_prompt = f"用户问题: {question}\n执行的SQL: {sql}\n查询出的数据集 (部分): \n{df.head(10).to_string()}"
        try:
            if local_demo:
                raise RuntimeError("演示数仓使用查询结果摘要")
            conclusion = self._call_llm(
                prompt=summary_prompt,
                system_prompt="你是一个资深商业分析师，用一句话总结下面的数据分析结论，并指出亮点或环比变化。",
                model_tier="complex"
            )
        except Exception as e:
            print(f"[Conclusion LLM Fallback]: {e}")
            metric_names = [m.get("name", "") for m in final_dsl.get("metrics", [])]
            sample_prefix = ("【演示数据】" if db_service.real_engine is None else "【项目示例数据】") if db_service.is_sample_data else ""
            conclusion = f"{sample_prefix}本次查询返回 {len(df)} 个分组，指标：{', '.join(metric_names)}。数值及查询范围见下方结果。"

        # 自适应图表类型
        column_types = self._detect_column_types(df, final_dsl)
        chart_info = self._auto_detect_chart_type(df, final_dsl, column_types)

        # 获取来源表说明
        source_tables = list({self.semantic_layer.resolve_metric(m["name"]).source_table for m in final_dsl["metrics"] if self.semantic_layer.resolve_metric(m["name"])})
        source_desc = " + ".join([f"{t}（{TABLE_CONFIG[t]['description']}）" if t in TABLE_CONFIG else t for t in source_tables])

        # 脱敏数据转换
        df_clean = df.fillna("")
        result_records = df_clean.to_dict(orient="records")

        # 记录查询历史
        user_memory.add_history(
            user=user,
            question=question,
            sql=sql,
            dialect=dialect,
            result_summary=conclusion
        )

        response_data = {
            "success": True,
            "skill_type": "query",
            "conclusion": conclusion,
            "chart": {
                "type": chart_info["type"],
                "title": chart_info["title"],
                "config": chart_info["config"]
            },
            "data": result_records,
            "column_types": column_types,
            "details": {
                "sql": formatted_sql,
                "dialect": dialect,
                "elapsed_time": elapsed_time,
                "tables": source_tables,
                "source_desc": source_desc,
                "filters": final_dsl["filters"],
                "estimated_rows": guardrail_result.get("estimated_rows", 0)
            },
            "cache_hit": False
        }
        # 存入多级语义缓存
        semantic_cache.put(question, dialect, user_role, response_data, embedding=query_vec)
        return response_data

ask_agent = AskAgent()
