# -*- coding: utf-8 -*-
from datetime import datetime
import json
import os

# NOTE: 用户记忆系统模型，存储和分析用户的查询历史，并生成画像偏好与主动推荐。

class UserMemory:
    def __init__(self, storage_path="/Users/mindezhi/DataWareHouse-Agent/backend/user_memory.json"):
        self.storage_path = storage_path
        self.history = []
        self.custom_preferences = {}
        self._load()

    def _load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.history = data.get("history", [])
                        self.custom_preferences = data.get("custom_preferences", {})
                    else:
                        self.history = data
                        self.custom_preferences = {}
            except Exception as e:
                print(f"Error loading user memory: {e}")
                self.history = []
                self.custom_preferences = {}
        else:
            # 预置一些历史数据以充实页面体验
            self.history = [
                {
                    "id": 1,
                    "user": "张三",
                    "question": "华东区过去30天GMV是多少",
                    "sql": "SELECT SUM(gmv) AS total_gmv FROM dws_trade_order_daily WHERE region_name = '华东' AND dt >= DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY)",
                    "dialect": "doris",
                    "execution_time": "0.02s",
                    "result_summary": "过去30天华东区GMV为 ¥1,234.50 万",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                },
                {
                    "id": 2,
                    "user": "张三",
                    "question": "过去6个月销售额趋势",
                    "sql": "SELECT DATE_TRUNC('month', dt) AS month, SUM(gmv) AS total_gmv FROM dws_trade_order_daily GROUP BY month ORDER BY month",
                    "dialect": "clickhouse",
                    "execution_time": "0.05s",
                    "result_summary": "近6月GMV呈稳步上升趋势，在 5 月达到峰值 ¥1,235 万",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            ]
            self.custom_preferences = {}
            self._save()

    def _save(self):
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                data = {
                    "history": self.history,
                    "custom_preferences": self.custom_preferences
                }
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving user memory: {e}")

    def add_history(self, user: str, question: str, sql: str, dialect: str, result_summary: str):
        """
        新增查询历史并触发偏好画像离线更新
        """
        record_id = len(self.history) + 1
        record = {
            "id": record_id,
            "user": user,
            "question": question,
            "sql": sql,
            "dialect": dialect,
            "execution_time": "0.01s",
            "result_summary": result_summary,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.history.insert(0, record) # 新记录排在最前
        self._save()
        return record

    def get_history(self, user: str, limit: int = 10):
        """
        L1 - 查询历史列表
        """
        return [h for h in self.history if h["user"] == user][:limit]

    def get_preference_profile(self, user: str) -> dict:
        """
        L2 - 基于历史行为提取用户偏好画像
        """
        user_history = [h for h in self.history if h["user"] == user]
        
        # 默认画像（在无历史记录时起效）
        profile = {
            "user": user,
            "common_tables": [{"table": "dws_trade_order_daily", "count": 5}],
            "common_metrics": [{"metric": "gmv", "count": 4}],
            "common_dimensions": [{"dimension": "region_name", "count": 3}],
            "common_time_ranges": [{"range": "过去30天", "count": 2}]
        }
        
        # 从 SQL 或是问题中简单提取偏好特征进行统计
        tables = {}
        metrics = {}
        dims = {}
        ranges = {}

        for h in user_history:
            q = h["question"]
            sql_lower = h["sql"].lower()

            # 统计表
            if "dws_trade_order_daily" in sql_lower:
                tables["dws_trade_order_daily"] = tables.get("dws_trade_order_daily", 0) + 1
            if "dim_region" in sql_lower:
                tables["dim_region"] = tables.get("dim_region", 0) + 1

            # 统计指标
            if "gmv" in sql_lower or "销售额" in q:
                metrics["gmv"] = metrics.get("gmv", 0) + 1
            if "refund_amount" in sql_lower or "退款额" in q:
                metrics["refund_amount"] = metrics.get("refund_amount", 0) + 1
            if "order_count" in sql_lower or "订单量" in q:
                metrics["order_count"] = metrics.get("order_count", 0) + 1

            # 统计维度
            if "region_name" in sql_lower or "区域" in q or "区" in q:
                dims["region_name"] = dims.get("region_name", 0) + 1
            if "category_name" in sql_lower or "品类" in q or "商品" in q:
                dims["category_name"] = dims.get("category_name", 0) + 1

            # 统计时间段
            if "30" in q or "30天" in q:
                ranges["过去30天"] = ranges.get("过去30天", 0) + 1
            if "趋势" in q or "按月" in q or "6个月" in q:
                ranges["趋势/近6月"] = ranges.get("趋势/近6月", 0) + 1

        # 排序整理成 TOP 列表
        def sort_dict(d, name_key):
            sorted_items = sorted(d.items(), key=lambda x: x[1], reverse=True)
            return [{name_key: k, "count": v} for k, v in sorted_items[:3]]

        if tables:
            profile["common_tables"] = sort_dict(tables, "table")
        if metrics:
            profile["common_metrics"] = sort_dict(metrics, "metric")
        if dims:
            profile["common_dimensions"] = sort_dict(dims, "dimension")
        if ranges:
            profile["common_time_ranges"] = sort_dict(ranges, "range")

        # 覆盖自定义偏好（如果存在）
        custom = self.custom_preferences.get(user, {})
        if custom:
            if "common_tables" in custom:
                profile["common_tables"] = custom["common_tables"]
            if "common_metrics" in custom:
                profile["common_metrics"] = custom["common_metrics"]
            if "common_dimensions" in custom:
                profile["common_dimensions"] = custom["common_dimensions"]
            if "common_time_ranges" in custom:
                profile["common_time_ranges"] = custom["common_time_ranges"]

        return profile

    def update_preference_profile(self, user: str, profile_update: dict):
        """
        手动覆盖/更新 L2 画像偏好
        """
        self.custom_preferences[user] = {
            "common_tables": profile_update.get("common_tables", []),
            "common_metrics": profile_update.get("common_metrics", []),
            "common_dimensions": profile_update.get("common_dimensions", []),
            "common_time_ranges": profile_update.get("common_time_ranges", [])
        }
        self._save()
        return self.get_preference_profile(user)

    def get_active_recommendations(self, user: str) -> list:
        """
        L3 - 主动建议
        根据用户偏好画像和相似模式生成推荐问题
        """
        profile = self.get_preference_profile(user)
        
        # 根据偏好生成推荐列表
        recommendations = []
        top_metric = profile["common_metrics"][0]["metric"] if profile["common_metrics"] else "gmv"
        top_dim = profile["common_dimensions"][0]["dimension"] if profile["common_dimensions"] else "region_name"

        metric_zh = "销售额 (GMV)" if top_metric == "gmv" else ("退款额" if top_metric == "refund_amount" else "订单量")
        dim_zh = "区域" if top_dim == "region_name" else "品类"

        # 主动推送可能感兴趣的查询
        recommendations.append(f"对比去年同期的 {metric_zh} 趋势")
        recommendations.append(f"各{dim_zh}的{metric_zh}排名分布")
        recommendations.append(f"过去30天各{dim_zh}退款率异常监控")

        return recommendations

user_memory = UserMemory()
