"""Phase 3.1：方言策略包。

从本包导入方言策略基类与工厂方法。
新增方言：在 base.DialectStrategy 注册即可。
"""

from app.repositories.dialect.base import DialectStrategy, get_dialect_strategy

__all__ = ["DialectStrategy", "get_dialect_strategy"]
