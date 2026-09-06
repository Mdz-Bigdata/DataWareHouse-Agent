import asyncio
import unittest
import uuid
import logging
from datetime import datetime
from sqlalchemy import select, delete
from app.core.orm import AsyncSessionLocal

# 强制导入所有模型以注册到 SQLAlchemy
from app.models.user import User
from app.models.agent import AIAgent, AIAgentVersion
from app.models.audit import AgentExecutionHistory, AgentExecutionTrace
from app.models.task import AgentScheduledTask
from app.models.permission import UserRoleRelation # 解决之前的报错

from app.services.ai.scheduler_service import scheduler_service
from apscheduler.triggers.interval import IntervalTrigger

class TestSchedulerIntegration(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        # 调低日志等级以查看调度细节
        logging.basicConfig(level=logging.INFO)

    def tearDown(self):
        self.loop.close()

    async def _verify_trigger(self):
        print("\n🚀 开始验证调度器触发逻辑 (极速版)...")
        
        async with AsyncSessionLocal() as session:
            # 1. 获取测试主体
            user_res = await session.execute(select(User).limit(1))
            user = user_res.scalar()
            agent_res = await session.execute(select(AIAgent).where(AIAgent.is_enabled == True).limit(1))
            agent = agent_res.scalar()
            
            if not user or not agent:
                print("❌ 错误: 数据库中缺少必要的用户或智能体数据。")
                return

            test_conv_id = f"test_fast_{uuid.uuid4().hex[:8]}"
            
            # 2. 创建一个特殊的测试任务记录
            new_task = AgentScheduledTask(
                name="FAST_TEST_TASK",
                user_id=user.id,
                agent_id=agent.id,
                conversation_id=test_conv_id,
                cron_expr="* * * * *", # 数据库存 Cron
                prompt="测试触发",
                status=1
            )
            session.add(new_task)
            await session.commit()
            await session.refresh(new_task)
            print(f"✅ 测试任务已准备: ID={new_task.id}")

            try:
                # 3. 手动启动调度器并覆盖触发器为每 5 秒一次
                await scheduler_service.start()
                
                # 覆盖刚才的任务，改用间隔触发以便快速验证
                job_id = f"task_{new_task.id}"
                scheduler_service._scheduler.reschedule_job(job_id, trigger=IntervalTrigger(seconds=5))
                
                print("⏳ 调度器已切换为 5 秒间隔模式，正在监控数据库...")

                triggered = False
                for i in range(20): # 最多等 20 秒
                    await asyncio.sleep(1)
                    
                    # 检查执行历史
                    history_stmt = select(AgentExecutionHistory).where(AgentExecutionHistory.conversation_id == test_conv_id)
                    history_res = await session.execute(history_stmt)
                    history_item = history_res.scalar()

                    if history_item:
                        print(f"\n✨ [第 {i} 秒] 检测到触发！")
                        print(f"   - Trace ID: {history_item.trace_id}")
                        print(f"   - AI 回复摘要: {history_item.summary[:50]}...")
                        triggered = True
                        break
                    
                    if i % 5 == 0:
                        print(f"   仍在等待中... ({i}s)")

                if not triggered:
                    print("\n❌ 触发失败: 在 20 秒内未产生历史记录。")
                    job = scheduler_service._scheduler.get_job(job_id)
                    print(f"   - 检查 Job 状态: {job}")

            finally:
                await scheduler_service.stop()
                await session.execute(delete(AgentScheduledTask).where(AgentScheduledTask.id == new_task.id))
                await session.commit()

    def test_scheduler(self):
        self.loop.run_until_complete(self._verify_trigger())

if __name__ == "__main__":
    unittest.main()
