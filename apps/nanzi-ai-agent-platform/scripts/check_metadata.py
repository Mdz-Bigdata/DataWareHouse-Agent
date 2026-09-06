import asyncio
import os
import sys
# Ensure app modules are visible
sys.path.append(os.getcwd())

from app.core.orm import AsyncSessionLocal
from app.services.metadata_service import MetadataService

async def check_data():
    async with AsyncSessionLocal() as db:
        print("🔍 Checking Datasets in DB...")
        datasets = await MetadataService.get_datasets(db)
        print(f"Found {len(datasets)} datasets.")
        for ds in datasets:
            print(f" - [ID: {ds.id}] {ds.name} (Tables: {len(ds.tables) if ds.tables else 'Not Loaded'})")
            
            # Force load tables to be sure
            full_ds = await MetadataService.get_dataset_by_id(db, ds.id, is_admin=True)
            if full_ds:
                print(f"   Tables in detail: {[t.physical_name for t in full_ds.tables]}")

if __name__ == "__main__":
    asyncio.run(check_data())
