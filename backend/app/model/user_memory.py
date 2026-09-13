# -*- coding: utf-8 -*-
from datetime import datetime
import json
import logging
import os
import tempfile

# NOTE: 用户记忆系统模型，存储和分析用户的查询历史，并生成画像偏好与主动推荐。
#
# 落盘位置通过 resolve_storage_path() 决定（环境变量优先），不再硬编码开发机路径；
# 写盘采用「临时文件 + 原子替换」，失败时记录真实日志并把失败状态暴露给调用方
# （persistence_status() / last_save_error），不再 print 一行就当作成功。

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_FILENAME = "user_memory.json"
_TRUTHY = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool = False, env=None) -> bool:
    env = os.environ if env is None else env
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in _TRUTHY


def resolve_storage_path(env=None) -> str:
    """决定用户记忆文件的落盘位置。

    优先级：
      1. USER_MEMORY_PATH —— 完整文件路径（容器里用它指向挂载卷，如
         /app/data/user_memory.json）；
      2. USER_MEMORY_DIR  —— 只给目录，文件名固定 user_memory.json；
      3. 兜底：后端包根目录下的 user_memory.json。开发机上等于原来的
         backend/user_memory.json（历史记忆不会因为这次改动而丢失），
         容器里（Dockerfile WORKDIR=/app）等于 /app/user_memory.json。
    """
    env = os.environ if env is None else env
    explicit = (env.get("USER_MEMORY_PATH") or "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    directory = (env.get("USER_MEMORY_DIR") or "").strip()
    if directory:
        return os.path.abspath(
            os.path.join(os.path.expanduser(directory), DEFAULT_MEMORY_FILENAME)
        )
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(package_root, DEFAULT_MEMORY_FILENAME)


class MemoryPersistenceError(RuntimeError):
    """用户记忆写盘失败。

    默认不抛出（写盘失败不应该让一次正常问数直接 500 —— 用户至少要拿到答案），
    调用方通过 persistence_status() 感知；把 USER_MEMORY_STRICT_PERSISTENCE=true
    打开后，所有改写记忆的方法会改为抛出本异常。
    """


def demo_seed_records():
    """明确标注的示例历史，仅在 USER_MEMORY_SEED_DEMO=true 时装载。

    这些数字是编造的，所以每条都带 is_demo=True，并且在用户名和结论文案里写死
    「示例」字样 —— 即便前端 schema 丢掉 is_demo 字段，界面上也不会把它误读成
    用户的真实问数历史。
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        {
            "id": 1,
            "user": "示例用户",
            "question": "【示例】华东区过去30天GMV是多少",
            "sql": "SELECT SUM(gmv) AS total_gmv FROM dws_trade_order_daily WHERE region_name = '华东' AND dt >= DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY)",
            "dialect": "doris",
            "execution_time": "0.02s",
            "result_summary": "【示例数据，非真实查询结果】过去30天华东区GMV为 ¥1,234.50 万",
            "created_at": stamp,
            "is_demo": True,
        },
        {
            "id": 2,
            "user": "示例用户",
            "question": "【示例】过去6个月销售额趋势",
            "sql": "SELECT DATE_TRUNC('month', dt) AS month, SUM(gmv) AS total_gmv FROM dws_trade_order_daily GROUP BY month ORDER BY month",
            "dialect": "clickhouse",
            "execution_time": "0.05s",
            "result_summary": "【示例数据，非真实查询结果】近6月GMV呈稳步上升趋势，在 5 月达到峰值 ¥1,235 万",
            "created_at": stamp,
            "is_demo": True,
        },
    ]


class UserMemory:
    def __init__(self, storage_path=None, strict_persistence=None, seed_demo=None):
        self.storage_path = storage_path or resolve_storage_path()
        self.strict_persistence = (
            _env_flag("USER_MEMORY_STRICT_PERSISTENCE", False)
            if strict_persistence is None else bool(strict_persistence)
        )
        self.seed_demo = (
            _env_flag("USER_MEMORY_SEED_DEMO", False) if seed_demo is None else bool(seed_demo)
        )
        self.history = []
        self.custom_preferences = {}
        self.error_corrections = []
        # 写盘健康状态：调用方/健康检查据此判断「记忆是不是真的存下去了」
        self.last_save_error = None
        self.last_save_error_at = None
        self.failed_save_count = 0
        self.load_error = None
        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _quarantine_corrupt_file(self):
        """把读不动的记忆文件改名存档，而不是让下一次 _save 直接覆盖掉。

        原实现解析失败后以空记忆继续跑，紧接着的一次写入就把原文件整个盖掉，
        用户的真实历史再也找不回来 —— 这同样是一种静默丢数。
        """
        backup_path = f"{self.storage_path}.corrupt-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        try:
            os.replace(self.storage_path, backup_path)
            return backup_path
        except OSError:
            logger.exception("隔离损坏的用户记忆文件失败：path=%s", self.storage_path)
            return None

    def _load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.history = data.get("history", [])
                    self.custom_preferences = data.get("custom_preferences", {})
                    self.error_corrections = data.get("error_corrections", [])
                else:
                    self.history = data
                    self.custom_preferences = {}
                    self.error_corrections = []
                self.load_error = None
                return
            except Exception as e:
                self.history = []
                self.custom_preferences = {}
                self.error_corrections = []
                backup_path = self._quarantine_corrupt_file()
                self.load_error = f"{type(e).__name__}: {e}"
                logger.error(
                    "用户记忆文件无法解析，已以空记忆启动；原文件备份至 %s：path=%s",
                    backup_path or "(备份失败)", self.storage_path, exc_info=True,
                )
                return

        # 文件不存在 = 全新部署，就是一段空历史。
        # 绝不再往这里塞编造的「¥1,234.50 万」当成用户的真实问数记录。
        self.history = list(demo_seed_records()) if self.seed_demo else []
        self.custom_preferences = {}
        self.error_corrections = []
        if self.seed_demo:
            logger.warning(
                "USER_MEMORY_SEED_DEMO 已开启，装载 %d 条带【示例】标记的演示历史：path=%s",
                len(self.history), self.storage_path,
            )
            self._save()

    def _save(self) -> bool:
        """把记忆写盘。成功返回 True，失败返回 False（严格模式下改为抛异常）。"""
        payload = {
            "history": self.history,
            "custom_preferences": self.custom_preferences,
            "error_corrections": self.error_corrections,
        }
        tmp_path = None
        try:
            directory = os.path.dirname(self.storage_path) or "."
            os.makedirs(directory, exist_ok=True)
            # 同目录临时文件 + os.replace：进程被杀或磁盘写满时，不会把已有的
            # 记忆截断成半个 JSON（那会在下次启动时变成「解析失败」）。
            fd, tmp_path = tempfile.mkstemp(prefix=".user_memory-", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.storage_path)
            tmp_path = None
        except Exception as e:
            self.failed_save_count += 1
            self.last_save_error = f"{type(e).__name__}: {e}"
            self.last_save_error_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.error(
                "用户记忆写入失败，本次改动只存在于内存中，重启即丢失：path=%s",
                self.storage_path, exc_info=True,
            )
            if self.strict_persistence:
                raise MemoryPersistenceError(
                    f"用户记忆写入失败（{self.storage_path}）：{self.last_save_error}"
                ) from e
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.warning("清理用户记忆临时文件失败：%s", tmp_path, exc_info=True)
        self.last_save_error = None
        self.last_save_error_at = None
        return True

    def persistence_status(self) -> dict:
        """记忆落盘健康状态，供调用方/健康检查感知「写进去了没有」。"""
        return {
            "storage_path": self.storage_path,
            "healthy": self.last_save_error is None and self.load_error is None,
            "last_save_error": self.last_save_error,
            "last_save_error_at": self.last_save_error_at,
            "failed_save_count": self.failed_save_count,
            "load_error": self.load_error,
            "strict_persistence": self.strict_persistence,
            "history_count": len(self.history),
            "error_correction_count": len(self.error_corrections),
        }

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
        return [h for h in self.history if h.get("user") == user][:limit]

    def get_preference_profile(self, user: str) -> dict:
        """
        L2 - 基于历史行为提取用户偏好画像
        """
        user_history = [h for h in self.history if h.get("user") == user]
        
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
            q = h.get("question") or ""
            sql_lower = (h.get("sql") or "").lower()

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
        from app.service.semantic_layer import semantic_layer

        profile = self.get_preference_profile(user)
        preferred = [entry["metric"] for entry in profile.get("common_metrics", [])]
        metric = next((found for name in preferred if (found := semantic_layer.resolve_metric(name))), None)
        if metric is None:
            metric = next((found for name in ("total_gmv", "total_play_count", "articles_count")
                           if (found := semantic_layer.resolve_metric(name))), None)
        if metric is None:
            return []
        available = semantic_layer.suggested_dimensions(metric)
        preferred_dimensions = [entry["dimension"] for entry in profile.get("common_dimensions", [])]
        choices = [name for name in preferred_dimensions if name in available]
        choices += [name for name in ("category_name", "source_platform", "region_name", "plan_name")
                    if name in available and name not in choices]
        choices += [name for name in available if name not in choices]
        # Canonical metric/table/field names keep suggestions unambiguous even
        # when multiple domains share human aliases such as “文章数量”.
        query = f"{metric.source_table}表的{metric.name}"
        if not choices:
            return [f"查询{query}"]
        dimension = choices[0]
        return [f"查询{query}按{dimension}分组统计",
                f"{query}按{dimension}排名前5",
                f"过去30天{query}按{dimension}变化归因"]

    def add_error_correction(self, question: str, error_message: str, wrong_sql: str, corrected_sql: str):
        """
        记录一条大模型物理纠错成功的经验
        """
        record = {
            "question": question,
            "error_message": error_message,
            "wrong_sql": wrong_sql,
            "corrected_sql": corrected_sql,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.error_corrections.append(record)
        self._save()
        return record

    def get_error_corrections(self) -> list:
        """
        获取所有被成功记录的纠错历史
        """
        return self.error_corrections

    def delete_error_correction(self, question: str) -> bool:
        """
        根据用户提问，删除特定的纠错记忆记录
        """
        original_len = len(self.error_corrections)
        self.error_corrections = [
            ec for ec in self.error_corrections 
            if ec.get("question", "").strip().lower() != question.strip().lower()
        ]
        if len(self.error_corrections) < original_len:
            self._save()
            return True
        return False

    def clear_error_corrections(self):
        """
        清空全部已保存的纠错记录
        """
        self.error_corrections = []
        self._save()

user_memory = UserMemory()
