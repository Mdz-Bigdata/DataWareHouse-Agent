import pytest
from unittest.mock import MagicMock, patch
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.tools.system_tools import get_current_time

pytestmark = pytest.mark.no_infrastructure

def test_system_implicit_tools_registry():
    """Verify that system implicit tools are correctly registered."""
    tools = ToolRegistry.get_system_implicit_tools()
    assert len(tools) >= 1
    tool_names = [t.name for t in tools]
    assert "get_current_time" in tool_names
    assert len(tool_names) == len(set(tool_names))

    assert {
        "create_recurring_task",
        "get_my_tasks",
        "cancel_task",
        "start_task",
        "pause_task",
        "run_task_manually",
        "update_user_preference",
        "fetch_user_long_term_memory",
        "memory_search",
        "create_skills",
        "list_available_skills",
        "read_skill_instruction",
        "send_portal_notification",
    }.issubset(set(tool_names))

    assert {
        "read_file",
        "write_file",
        "exec_command",
        "list_process",
        "manage_process",
        "search_text",
        "system_http_request",
        "web_renderer_and_snapshot",
        "send_dingtalk_message",
        "send_email",
        "send_wechat_work_message",
    }.isdisjoint(set(tool_names))
    assert "execute_system_command" not in tool_names
    assert "manage_system_process" not in tool_names

def test_get_current_time_function():
    """Verify the functionality of get_current_time."""
    # Test default
    time_str = get_current_time.invoke({})
    assert isinstance(time_str, str)
    assert ":" in time_str
    
    # Test specific timezone
    time_utc = get_current_time.invoke({"timezone": "UTC"})
    assert "+0000" in time_utc or "UTC" in time_utc


def test_get_current_time_is_read_only_without_approval():
    from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

    spec = runtime_tool_spec_from_legacy_tool(get_current_time, source_type="system")
    assert spec.permission_scope == "read"
    assert spec.is_read_only is True

if __name__ == "__main__":
    # Manual run check
    print("Testing registry...")
    test_system_implicit_tools_registry()
    print("Registry OK.")
    
    print("Testing function...")
    test_get_current_time_function()
    print(f"Function output: {get_current_time.invoke({})}")
    print("Function OK.")
