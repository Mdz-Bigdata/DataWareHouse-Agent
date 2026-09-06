import asyncio
import json
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai.dispatcher import AgentDispatcher
from app.services.ai.executors.openclaw_executor import OpenClawExecutor
from app.schemas.agent import ChatConfig

# --- Mock 异步迭代器 (改进版：确保正确吐出 SSE 行) ---

class MockAsyncIterator:
    def __init__(self, items):
        self.items = items
    def __aiter__(self):
        return self
    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)

class MockResponse:
    def __init__(self, lines):
        self.status_code = 200
        self.lines = lines
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    def aiter_lines(self):
        # 必须是异步迭代器
        return MockAsyncIterator(self.lines)
    async def aread(self):
        return b""

async def test_openclaw_verify_return():
    """
    终极链路验证：确保英文用户名透传，并能正确解析流式返回内容。
    """
    # 1. 配置
    agent_config = ChatConfig(
        agent_id="123",
        agent_name="openclaw-tester",
        engine_type="OPENCLAW",
        engine_config={
            "base_url": "https://yunshu-openclaw.yovole.net",
            "api_key": "sk-test-key",
            "model": "bot-test-id"
        },
        model_name="openclaw-v1",
        temperature=0.0,
        system_prompt="Test",
        tools=[],
        capabilities=[]
    )

    # 模拟用户信息 (使用英文 user_name)
    user_info = {
        "user_id": "1",
        "user_name": "chenxiaolong", # 期待透传的值
        "real_name": "陈小龙",
        "role": "admin"
    }
    
    messages = [{"role": "user", "content": "你好"}]
    
    # 模拟 OpenClaw 返回的标准 SSE 消息行
    mock_sse_lines = [
        'data: {"choices": [{"delta": {"content": "你好"}}]}',
        '',
        'data: {"choices": [{"delta": {"content": "，我是 OpenClaw 助手"}}]}',
        '',
        'data: [DONE]',
        ''
    ]

    print("\n🚀 [1/3] 执行 OpenClaw 引擎调度...")
    executor = await AgentDispatcher.dispatch(
        agent_config, "你好", messages, "trace-id", [], {}, user_info
    )

    print("\n🚀 [2/3] 模拟 API 调用并拦截请求与返回...")
    
    with patch("httpx.AsyncClient.stream") as mock_stream:
        mock_stream.return_value = MockResponse(mock_sse_lines)

        full_answer = ""
        async for chunk in executor.execute(messages):
            if "content" in chunk and "type" not in chunk: # Reply Content
                content = chunk.get("content", "")
                full_answer += content
                print(f"  📥 收到流式内容: '{content}'")
            elif chunk.get("type") == "log":
                print(f"  📜 [LOG] {chunk['title']}: {chunk['details']}")

        # 验证结果
        print("\n🚀 [3/3] 结果断言验证:")
        
        # 1. 验证用户名 (英文 user_name)
        request_payload = mock_stream.call_args.kwargs["json"]
        sent_user = request_payload["user"]
        print(f"  - 发送给 OpenClaw 的 user 参数: '{sent_user}'")
        assert sent_user == "chenxiaolong"

        # 2. 验证路径 (修复后的路径)
        request_url = mock_stream.call_args[0][1]
        print(f"  - 发送给 OpenClaw 的 URL: {request_url}")
        assert "/v1/chat/completions" in request_url

        # 3. 验证返回内容 (SSE 解析成功)
        print(f"  - 完整回答内容: '{full_answer}'")
        assert full_answer == "你好，我是 OpenClaw 助手"

        print("\n✨ [SUCCESS] 测试脚本完美运行！用户名透传与内容返回解析全部正常。")

if __name__ == "__main__":
    asyncio.run(test_openclaw_verify_return())
