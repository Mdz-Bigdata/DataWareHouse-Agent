"""Serialize and restore DataRunState for AgentScope session persistence."""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector
from app.services.ai.runners.chatbi.run_state import DataRunState


def data_run_state_to_pending_state(
    state: DataRunState,
    stream_meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        **stream_meta,
        "data_run_state": asdict(state),
    }


def sync_pending_data_run_state(
    state: DataRunState,
    pending_state: dict[str, Any],
) -> None:
    pending_state["data_run_state"] = asdict(state)


def build_stream_state(
    state: DataRunState,
    stream_meta: dict[str, Any],
) -> dict[str, Any]:
    stream_state = data_run_state_to_pending_state(state, stream_meta)
    stream_state["tool_names"] = state.tool_names
    stream_state["tool_args_text"] = state.tool_args_text
    stream_state["tool_outputs"] = state.tool_outputs
    stream_state["tool_started_at"] = state.tool_started_at
    stream_state.setdefault("tool_data", {})
    return stream_state


def pending_state_to_data_run_state(
    pending_state: dict[str, Any],
) -> tuple[DataRunState, dict[str, Any]]:
    raw = pending_state.get("data_run_state") or {}
    valid_keys = {field.name for field in fields(DataRunState)}
    kwargs = {key: raw[key] for key in valid_keys if key in raw}
    detector_raw = kwargs.get("tool_loop_detector")
    if isinstance(detector_raw, dict):
        detector_keys = {field.name for field in fields(ToolLoopDetector)}
        try:
            kwargs["tool_loop_detector"] = ToolLoopDetector(
                **{key: detector_raw[key] for key in detector_keys if key in detector_raw}
            )
        except Exception:
            kwargs["tool_loop_detector"] = ToolLoopDetector()
    data_state = DataRunState(**kwargs)
    stream_meta = {
        key: pending_state[key]
        for key in ("system_content", "max_steps")
        if key in pending_state
    }
    return data_state, stream_meta
