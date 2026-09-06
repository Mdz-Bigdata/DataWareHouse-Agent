import time

import jieba.analyse
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def extract_keywords(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """runtime参数包括 1.运行上下文runtime_context 2.store持久化 3.stream_writer流写入器"""
    started_at = time.perf_counter()
    # 1.获取流写入器
    writer = runtime.stream_writer
    # 2.写回正在运行状态
    writer({"type": "progress", "step": "抽取关键词", "status": "running"})
    try:
        # 1.从 state 中获取用户提出的问题
        query = state["query"]
        # 2.调用 jieba 分词器获取关键词
        # 定义返回指定词性的元组
        allow_pos = (
            "n",  # 名词: 数据、服务器、表格
            "nr",  # 人名: 张三、李四
            "ns",  # 地名: 北京、上海
            "nt",  # 机构团体名: 政府、学校、某公司
            "nz",  # 其他专有名词: Unicode、哈希算法、诺贝尔奖
            "v",  # 动词: 运行、开发
            "vn",  # 名动词: 工作、研究
            "a",  # 形容词: 美丽、快速
            "an",  # 名形词: 难度、合法性、复杂度
            "eng",  # 英文
            "i",  # 成语
            "l",  # 常用固定短语
        )
        keywords = jieba.analyse.extract_tags(query, topK=10, allowPOS=allow_pos)
        analysis_plan = state.get("analysis_plan", {})
        keywords.extend(analysis_plan.get("metric_hints", []))
        keywords.extend(analysis_plan.get("dimensions", []))
        for term in state.get("semantic_terms", []):
            keywords.append(str(term.get("standard_term") or ""))
            keywords.extend(str(value) for value in term.get("synonyms", []))
        keywords.append(query)
        keywords = list(dict.fromkeys(keyword for keyword in keywords if keyword))
        # 4.业务没有异常，写回成功状态
        writer(
            {
                "type": "progress",
                "step": "抽取关键词",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.info(f"关键词：{keywords}")
        # 3.3 更新state
        return {"keywords": keywords}
    except Exception as e:
        # 5.业务异常，写回错误状态，抛出异常
        writer(
            {
                "type": "progress",
                "step": "抽取关键词",
                "status": "error",
                "message": str(e),
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        logger.error(f"抽取关键词失败:{e}")
        raise


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
