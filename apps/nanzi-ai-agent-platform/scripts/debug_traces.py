import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionTrace
from sqlalchemy import select, desc

async def check_traces():
    async with AsyncSessionLocal() as session:
        # Get latest 20 traces
        stmt = select(AgentExecutionTrace).order_by(desc(AgentExecutionTrace.created_at)).limit(20)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        
        print(f"Found {len(rows)} recent trace logs.")
        print("-" * 60)
        if not rows:
            print("❌ No traces found! Data is not being saved.")
            return

        # Group by Trace ID to see how many steps per trace
        trace_counts = {}
        for row in rows:
            trace_id = row.trace_id
            if trace_id not in trace_counts:
                trace_counts[trace_id] = []
            trace_counts[trace_id].append(row)
            
        for tid, steps in trace_counts.items():
            first_time = steps[-1].created_at # roughly
            print(f"Trace ID: {tid}")
            print(f"  Timestamp: {first_time}")
            print(f"  Step Count (in this batch): {len(steps)}")
            for step in sorted(steps, key=lambda x: x.step_number):
                print(f"    - Step {step.step_number}: [{step.event_type}] Tool: {step.tool_name} (Status: {step.status})")
            print("-" * 30)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(check_traces())
