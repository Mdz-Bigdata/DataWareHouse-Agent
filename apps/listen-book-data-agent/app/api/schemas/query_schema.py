from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr

type QueryParameterValue = StrictBool | StrictInt | StrictFloat | StrictStr | None


class QuerySchema(BaseModel):
    """
        在fastapi框架跟深度使用pydantic类型好处轻松实现参数校验
        处理前端提问提交请求体参数
    """
    query: str = Field(
        min_length=1,
        max_length=500,
        description="用户提出的问题",
        title="问题",
    )
    parameters: dict[str, QueryParameterValue] = Field(
        default_factory=dict,
        description="发布可信案例使用的命名参数；不传时保持原请求兼容",
    )
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    parent_trace_id: str | None = Field(default=None, min_length=1, max_length=36)
