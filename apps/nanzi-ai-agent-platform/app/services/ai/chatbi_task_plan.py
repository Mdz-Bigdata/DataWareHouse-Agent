"""Restricted, dependency-aware task plans for explicit mixed ChatBI requests."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum


class ChatBITaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ChatBITask:
    task_id: str
    operation: str
    query: str
    depends_on: list[str] = field(default_factory=list)
    status: ChatBITaskStatus = ChatBITaskStatus.PENDING
    error: str | None = None


@dataclass
class ChatBITaskPlan:
    plan_id: str
    tasks: list[ChatBITask]
    version: int = 1

    @property
    def is_mixed(self) -> bool:
        return len(self.tasks) > 1

    @property
    def is_executable(self) -> bool:
        """Only operations implemented by the recursive ChatBI runner may be split."""
        return self.is_mixed and all(
            task.operation in {"query", "analyze", "present"} for task in self.tasks
        )

    def as_event(self) -> dict:
        return {
            "type": "chatbi_task_plan",
            "data": {
                "version": self.version,
                "plan_id": self.plan_id,
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "operation": task.operation,
                        "query": task.query,
                        "depends_on": task.depends_on,
                        "status": task.status.value,
                    }
                    for task in self.tasks
                ],
            },
        }


_SEQUENCE_RE = re.compile(
    r"(?:\s*(?:，|,|；|;)\s*(?:然后|随后|接着|之后|再)\s*"
    r"|\s+(?:然后|随后|接着|之后|再)\s*)"
)


def _operation(text: str) -> str:
    q = text.lower()
    if any(word in q for word in ("简报", "汇报材料", "汇报稿")):
        return "brief"
    if any(word in q for word in ("监控", "订阅", "告警", "提醒")):
        return "monitor"
    if any(word in q for word in ("导出", "下载", "发送", "发给", "分享", "保存")):
        return "action"
    if any(word in q for word in ("图表", "柱状图", "折线图", "饼图", "可视化")):
        return "present"
    if any(word in q for word in ("分析", "原因", "归因", "总结", "结论", "变化")):
        return "analyze"
    return "query"


def build_chatbi_task_plan(user_question: str) -> ChatBITaskPlan:
    raw = str(user_question or "").strip()
    parts = [part.strip() for part in _SEQUENCE_RE.split(raw) if part.strip()]
    if parts:
        parts[0] = re.sub(r"^先(?:帮我)?", "", parts[0]).strip() or parts[0]
    tasks: list[ChatBITask] = []
    for index, part in enumerate(parts or [raw]):
        task_id = f"task_{index + 1}_{uuid.uuid4().hex[:6]}"
        dependencies = [tasks[-1].task_id] if tasks else []
        tasks.append(ChatBITask(
            task_id=task_id,
            operation=_operation(part),
            query=part,
            depends_on=dependencies,
        ))
    return ChatBITaskPlan(plan_id=f"plan_{uuid.uuid4().hex[:10]}", tasks=tasks)


def resolve_runnable_tasks(plan: ChatBITaskPlan) -> tuple[list[ChatBITask], list[ChatBITask]]:
    by_id = {task.task_id: task for task in plan.tasks}
    runnable: list[ChatBITask] = []
    skipped: list[ChatBITask] = []
    changed = True
    while changed:
        changed = False
        for task in plan.tasks:
            if task.status != ChatBITaskStatus.PENDING:
                continue
            dependencies = [by_id[item] for item in task.depends_on if item in by_id]
            if any(dep.status in {ChatBITaskStatus.FAILED, ChatBITaskStatus.SKIPPED} for dep in dependencies):
                task.status = ChatBITaskStatus.SKIPPED
                task.error = "dependency_failed"
                skipped.append(task)
                changed = True
            elif all(dep.status == ChatBITaskStatus.SUCCEEDED for dep in dependencies):
                runnable.append(task)
    return runnable, skipped
