"""Activate one configured data source at a time, keeping metadata consistent with it.

Switching rebinds the shared singletons in place so every module that imported
them observes the new source, and each source keeps its own connection pool and
semantic model so switching back does not rediscover metadata.
"""
from __future__ import annotations

from threading import RLock
from typing import Any

from app.service.data_sources import DEMO_SOURCE_ID, DataSource, configured_sources, find_source


class DataSourceError(RuntimeError):
    """A source cannot be selected, and the active source is left untouched."""


class DataSourceManager:
    def __init__(self):
        self._lock = RLock()
        # Each entry owns the state dictionaries of one source. Caching the
        # dictionaries rather than the objects is what makes switching reversible:
        # rebinding hands the singleton a different dictionary and leaves the
        # previous one intact for when that source is selected again.
        self._cache: dict[str, tuple[dict, dict]] = {}
        self._active_id: str | None = None

    @property
    def active_id(self) -> str | None:
        return self._active_id

    def adopt_current(self) -> str:
        """Record the source the process started on, without reconnecting to it."""
        from app.service.db_service import db_service
        from app.service.semantic_layer import semantic_layer
        with self._lock:
            if self._active_id is None:
                self._active_id = self._identify(db_service)
                self._cache[self._active_id] = (db_service.__dict__, semantic_layer.__dict__)
            return self._active_id

    @staticmethod
    def _identify(database) -> str:
        from app.service.data_sources import normalize_engine
        if database.real_engine is None:
            return DEMO_SOURCE_ID
        engine = normalize_engine(database.active_db_type)
        return next((source.id for source in configured_sources()
                     if source.engine == engine and source.origin in {"env", "config"}),
                    f"active-{engine}")

    def _build(self, source: DataSource):
        from app.service.db_service import DBService
        from app.service.semantic_layer import SemanticLayer
        try:
            database = DBService(source=source)
        except RuntimeError as error:
            raise DataSourceError(str(error)) from None
        return database.__dict__, SemanticLayer(database=database).__dict__

    def activate(self, source_id: str) -> dict[str, Any]:
        from app.service.db_service import db_service
        from app.service.semantic_layer import semantic_layer
        with self._lock:
            self.adopt_current()
            if source_id == self._active_id:
                return self.describe_active()
            # An already-built source keeps its verified connection, so returning
            # to it never depends on the configuration still being resolvable.
            if source_id not in self._cache:
                source = find_source(source_id)
                if source is None:
                    raise DataSourceError(f"数据源 '{source_id}' 不存在或未配置。")
                if not source.available:
                    raise DataSourceError(source.public()["unavailable_reason"] or "该数据源当前不可用。")
                self._cache[source_id] = self._build(source)
            database_state, layer_state = self._cache[source_id]
            # Rebinding the instance dictionaries keeps every existing import of
            # the singletons pointing at the newly selected source's state.
            db_service.__dict__ = database_state
            semantic_layer.__dict__ = layer_state
            self._active_id = source_id
            self._refresh_derived_metadata()
            return self.describe_active()

    @staticmethod
    def _refresh_derived_metadata() -> None:
        """Recall indices describe the previous source until they are rebuilt."""
        try:
            from app.service.vector_service import vector_service
            vector_service.ingest_metadata()
        except Exception as error:  # Recall degrades to deterministic parsing.
            print(f"[DataSource] 切换后重建向量元数据失败：{error}")

    def describe_active(self) -> dict[str, Any]:
        from app.service.data_source_info import describe_data_source
        from app.service.db_service import db_service
        info = describe_data_source(db_service)
        info["source_id"] = self.adopt_current()
        return info

    def catalog(self) -> dict[str, Any]:
        active = self.adopt_current()
        return {"active_id": active,
                "sources": [source.public(active) for source in configured_sources()]}


data_source_manager = DataSourceManager()
