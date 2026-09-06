from contextvars import ContextVar
from typing import Any, Dict, Optional

# 存储当前正在执行的 AI 会话请求所归属的用户元数据（user_info）
current_user_info: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_user_info", default=None)
