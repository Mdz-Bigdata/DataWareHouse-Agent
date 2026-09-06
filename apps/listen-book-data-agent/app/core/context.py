import asyncio
from contextvars import ContextVar

# 定义上下文变量request_id_ctx_var，用于在异步/多请求场景下存储和获取当前请求的唯一标识request_id
# 参数1："request_id" - 上下文变量的名称，用于标识该变量的用途
# 参数2：default=1 - 默认值，当未显式设置request_id时，获取到的值为1
request_id_ctx_var = ContextVar("request_id", default=1)

def set_request_id(request_id):
    """
    存入请求ID  fastAPI web层中间件 请求前生成请求ID存入上下文变量
    :param request_id:
    :return:
    """
    request_id_ctx_var.set(request_id)

def get_request_id():
    """
    从上下文中获取请求ID
    :return:
    """
    return request_id_ctx_var.get()
