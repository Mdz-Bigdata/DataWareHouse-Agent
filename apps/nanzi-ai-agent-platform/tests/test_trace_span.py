import sys
import os
import time
import pytest

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext, current_span_var

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_trace_span_nesting_and_inheritance():
    # 准备一个空 buffer 记录 Span 步骤
    trace_buffer = []
    
    # 初始状态下 current_span_var 应该为 None
    assert current_span_var.get() is None
    
    # 模拟外部大步骤：DataAgentRunner
    async with TraceSpanContext(
        trace_buffer=trace_buffer,
        event_type="agent_execution",
        span_name="DataAgentRunner"
    ) as outer_span:
        outer_id = outer_span.span_id
        assert outer_id is not None
        assert current_span_var.get() == outer_id
        
        # 嵌套第一层子步骤：get_dataset_schema 工具调用
        async with TraceSpanContext(
            trace_buffer=trace_buffer,
            event_type="tool_call",
            span_name="get_dataset_schema",
            tool_name="get_dataset_schema",
            tool_input={"query": "能耗"}
        ) as sub1_span:
            sub1_id = sub1_span.span_id
            assert sub1_id is not None
            assert sub1_span.parent_span_id == outer_id
            assert current_span_var.get() == sub1_id
            
        # 退出第一层后，应该恢复为 outer_id
        assert current_span_var.get() == outer_id
        
        # 嵌套第二层子步骤：execute_sql_query 工具调用
        async with TraceSpanContext(
            trace_buffer=trace_buffer,
            event_type="tool_call",
            span_name="execute_sql_query",
            tool_name="execute_sql_query"
        ) as sub2_span:
            sub2_id = sub2_span.span_id
            assert sub2_span.parent_span_id == outer_id
            assert current_span_var.get() == sub2_id
            
            # 再在里面嵌套一个三级微服务执行步骤
            async with TraceSpanContext(
                trace_buffer=trace_buffer,
                event_type="db_query",
                span_name="MySQLAdapter"
            ) as sub_sub_span:
                assert sub_sub_span.parent_span_id == sub2_id
                assert current_span_var.get() == sub_sub_span.span_id
                
            # 恢复为 sub2_id
            assert current_span_var.get() == sub2_id
            
        # 恢复为 outer_id
        assert current_span_var.get() == outer_id
        
    # 退出最外层，恢复为 None
    assert current_span_var.get() is None
    
    # 校验 trace_buffer 的记录
    assert len(trace_buffer) == 4
    
    # 1. 外部步骤
    step_0 = trace_buffer[0]
    assert step_0.event_type == "agent_execution"
    assert step_0.agent_name == "DataAgentRunner"
    assert step_0.parent_span_id is None
    assert step_0.status == "success"
    assert step_0.execution_time_ms > 0
    
    # 2. get_dataset_schema
    step_1 = trace_buffer[1]
    assert step_1.event_type == "tool_call"
    assert step_1.agent_name == "get_dataset_schema"
    assert step_1.parent_span_id == step_0.span_id
    assert step_1.status == "success"
    
    # 3. execute_sql_query
    step_2 = trace_buffer[2]
    assert step_2.parent_span_id == step_0.span_id
    
    # 4. MySQLAdapter
    step_3 = trace_buffer[3]
    assert step_3.parent_span_id == step_2.span_id


@pytest.mark.asyncio
async def test_trace_span_exception_handling():
    trace_buffer = []
    
    # 测试崩溃捕获，以防状态未能清理
    try:
        async with TraceSpanContext(
            trace_buffer=trace_buffer,
            event_type="test_event",
            span_name="TestErrorSpan"
        ):
            assert current_span_var.get() is not None
            raise ValueError("Test error message")
    except ValueError:
        pass
        
    # 异常抛出后，current_span_var 应该被正常 reset 清空
    assert current_span_var.get() is None
    
    # 校验崩溃是否被正确记录在 step 状态中
    assert len(trace_buffer) == 1
    step = trace_buffer[0]
    assert step.status == "failed"
    assert "Test error message" in step.error_message


if __name__ == "__main__":
    pytest.main([__file__])
