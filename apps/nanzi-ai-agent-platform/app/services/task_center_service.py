import logging
from typing import List, Optional, Dict, Any
from sqlalchemy import select, update, delete, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import uuid

from app.models.task import AgentScheduledTask
from app.services.ai.scheduler_service import scheduler_service, _task_run_conversation_prefix
from app.models.audit import AgentExecutionHistory

logger = logging.getLogger(__name__)


def attach_task_metrics(task: AgentScheduledTask) -> AgentScheduledTask:
    cfg = task.config if isinstance(task.config, dict) else {}
    metrics = cfg.get("task_metrics") if isinstance(cfg.get("task_metrics"), dict) else {}
    task.trigger_count = int(metrics.get("trigger_count") or 0)
    task.success_count = int(metrics.get("success_count") or task.run_count or 0)
    task.failure_count = int(metrics.get("failure_count") or 0)
    task.skipped_count = int(metrics.get("skipped_count") or 0)
    task.consecutive_failures = int(metrics.get("consecutive_failures") or 0)
    task.health_status = metrics.get("health_status") or ("healthy" if task.run_count else "unknown")
    task.last_status = metrics.get("last_status")
    task.last_message = metrics.get("last_message")
    task.last_error = metrics.get("last_error")
    task.last_attempt_at = metrics.get("last_started_at") or metrics.get("last_finished_at")
    task.last_finished_at = metrics.get("last_finished_at")
    task.last_alert_at = metrics.get("last_alert_at")
    return task

class TaskCenterService:
    @staticmethod
    async def create_task(
        db: AsyncSession,
        user_id: int,
        name: str,
        agent_id: str,
        cron_expr: str,
        prompt: str,
        source: str = "web",
        config: Optional[Dict[str, Any]] = None
    ) -> AgentScheduledTask:
        # Generate a dedicated conversation_id for this task
        conversation_id = f"task_conv_{uuid.uuid4().hex[:12]}"
        
        new_task = AgentScheduledTask(
            name=name,
            user_id=user_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            cron_expr=cron_expr,
            prompt=prompt,
            source=source,
            config=config,
            status=1 # Running by default
        )
        
        db.add(new_task)
        await db.commit()
        await db.refresh(new_task)
        
        # Sync to scheduler
        await scheduler_service.upsert_task(new_task)
        
        return new_task

    @staticmethod
    async def list_tasks(
        db: AsyncSession,
        user_id: Optional[int] = None,
        is_admin: bool = False
    ) -> List[Any]:
        from app.models.user import User
        from app.models.agent import AIAgent
        
        stmt = (
            select(AgentScheduledTask, User.real_name, User.user_name, AIAgent.display_name)
            .outerjoin(User, AgentScheduledTask.user_id == User.id)
            .outerjoin(AIAgent, AgentScheduledTask.agent_id == AIAgent.id)
        )
        
        if not is_admin and user_id:
            stmt = stmt.where(AgentScheduledTask.user_id == user_id)
        
        stmt = stmt.order_by(desc(AgentScheduledTask.created_at))
        result = await db.execute(stmt)
        rows = result.all()
        
        tasks = []
        for task_obj, real_name, user_name, agent_name in rows:
            # We attach creator_name and agent_name attribute for Pydantic to pick up
            task_obj.creator_name = real_name or user_name or f"User:{task_obj.user_id}"
            task_obj.agent_name = agent_name or task_obj.agent_id or "Unknown Agent"
            task_obj.next_run_at = scheduler_service.get_next_run_time(task_obj.id)
            attach_task_metrics(task_obj)
            tasks.append(task_obj)
            
        return tasks

    @staticmethod
    async def get_task(db: AsyncSession, task_id: int) -> Optional[AgentScheduledTask]:
        stmt = select(AgentScheduledTask).where(AgentScheduledTask.id == task_id)
        result = await db.execute(stmt)
        task = result.scalar_one_or_none()
        if task:
            task.next_run_at = scheduler_service.get_next_run_time(task.id)
            attach_task_metrics(task)
        return task

    @staticmethod
    async def update_task(
        db: AsyncSession,
        task_id: int,
        updates: Dict[str, Any]
    ) -> Optional[AgentScheduledTask]:
        stmt = update(AgentScheduledTask).where(AgentScheduledTask.id == task_id).values(**updates)
        await db.execute(stmt)
        await db.commit()
        
        updated_task = await TaskCenterService.get_task(db, task_id)
        if updated_task:
            await scheduler_service.upsert_task(updated_task)
        return updated_task

    @staticmethod
    async def delete_task(db: AsyncSession, task_id: int):
        task = await TaskCenterService.get_task(db, task_id)
        if task:
            # Stop in scheduler
            task.status = 0
            await scheduler_service.upsert_task(task)
            
            # Delete from DB
            await db.execute(delete(AgentScheduledTask).where(AgentScheduledTask.id == task_id))
            await db.commit()

    @staticmethod
    async def get_task_logs(
        db: AsyncSession,
        task_id: int,
        page: int = 1,
        page_size: int = 10
    ):
        task = await TaskCenterService.get_task(db, task_id)
        if not task:
            return [], 0
            
        run_prefix = _task_run_conversation_prefix(task.conversation_id)

        # Scheduled task runs use isolated run conversation IDs, but all runs keep
        # the task's root conversation prefix so the task log drawer can aggregate them.
        stmt = (
            select(AgentExecutionHistory)
            .where(
                or_(
                    AgentExecutionHistory.conversation_id == task.conversation_id,
                    AgentExecutionHistory.conversation_id.like(f"{run_prefix}_run_%"),
                )
            )
            .order_by(desc(AgentExecutionHistory.created_at))
        )
        
        # Pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0
        
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return result.scalars().all(), total
