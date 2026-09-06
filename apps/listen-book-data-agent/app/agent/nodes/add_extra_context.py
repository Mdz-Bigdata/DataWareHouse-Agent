from datetime import datetime

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, DateInfoState, DBInfoState
from app.core.log import logger


async def add_extra_context(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 1.获取流写入器
    writer = runtime.stream_writer
    # 2.写回正在运行状态
    writer({"type": "progress", "step": "添加额外上下文", "status": "running"})
    try:
        # 3.业务逻辑
        # 3.1 获取当前日期信息 解决问题中相对时间
        today = datetime.today()
        # 日期
        date = today.strftime("%Y-%m-%d")
        # 星期
        weekday = today.strftime("%A")
        # 季度
        quarter = f"Q{(today.month - 1) // 3 + 1}"
        date_info = DateInfoState(
            date=date,
            weekday=weekday,
            quarter=quarter
        )
        # 3.2 查询数仓得到数据库方言、数据库版本信息
        dw_mysql_repository = runtime.context["dw_mysql_repository"]
        db_info_dict:dict[str, str] = await dw_mysql_repository.get_db_info()
        db_info = DBInfoState(
            dialect=db_info_dict["dialect"],
            version=db_info_dict["version"]
        )
        # 3.3.业务没有异常，写回成功状态
        writer({"type": "progress", "step": "添加额外上下文", "status": "success"})
        logger.info(f"添加额外上下文成功：{date_info}, {db_info}")
        # 3.4 更新state
        return {"date_info": date_info, "db_info": db_info}
    except Exception as e:
        # 5.业务异常，写回错误状态，抛出异常
        writer({"type": "progress", "step": "添加额外上下文", "status": "error"})
        logger.error(f"添加额外上下文失败:{e}")
        raise