import pytest

from app.services.ai.chatbi_task_plan import (
    ChatBITaskStatus,
    build_chatbi_task_plan,
    resolve_runnable_tasks,
)


pytestmark = pytest.mark.no_infrastructure


def test_build_mixed_query_analyze_brief_plan_in_dependency_order():
    plan = build_chatbi_task_plan(
        "先查询本月各区域销售额，然后分析主要变化，再生成业务简报"
    )
    assert [task.operation for task in plan.tasks] == ["query", "analyze", "brief"]
    assert plan.tasks[1].depends_on == [plan.tasks[0].task_id]
    assert plan.tasks[2].depends_on == [plan.tasks[1].task_id]
    assert plan.is_executable is False


def test_only_runner_supported_mixed_operations_are_executable():
    plan = build_chatbi_task_plan("先查询本月销售额，然后分析变化，再生成折线图")
    assert [task.operation for task in plan.tasks] == ["query", "analyze", "present"]
    assert plan.is_executable is True


def test_delivery_wording_is_not_misclassified_as_query():
    plan = build_chatbi_task_plan("先查询本月销售额，然后把结果发给老板")
    assert [task.operation for task in plan.tasks] == ["query", "action"]
    assert plan.is_executable is False


def test_single_business_sentence_is_not_over_split():
    plan = build_chatbi_task_plan("查询本月各区域销售额和订单量")
    assert len(plan.tasks) == 1
    assert len(build_chatbi_task_plan("查询最近30天销售趋势").tasks) == 1


def test_failed_dependency_skips_only_dependent_tasks():
    plan = build_chatbi_task_plan("先查销售额，然后分析原因，再生成简报")
    plan.tasks[0].status = ChatBITaskStatus.FAILED
    runnable, skipped = resolve_runnable_tasks(plan)
    assert runnable == []
    assert [task.operation for task in skipped] == ["analyze", "brief"]
