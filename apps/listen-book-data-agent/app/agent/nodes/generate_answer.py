import time

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.services.grounded_answer_service import build_grounded_answer


async def generate_answer(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Emit a data-grounded explanation after the warehouse query completes."""

    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "生成结果解释", "status": "running"})
    try:
        answer = build_grounded_answer(
            sql=state["sql"],
            rows=state.get("result_rows", []),
            metric_infos=state.get("metric_infos", []),
            analysis_plan=state.get("analysis_plan", {}),
        ).to_event()
        writer({"type": "answer", **answer})
        writer(
            {
                "type": "progress",
                "step": "生成结果解释",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.info("已生成基于查询结果的解释，返回 {} 行", answer["row_count"])
        return {"answer": answer}
    except Exception as exc:
        writer(
            {
                "type": "progress",
                "step": "生成结果解释",
                "status": "error",
                "message": str(exc),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.exception("生成结果解释失败")
        raise


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
