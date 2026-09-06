import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.metadata import MetaDataset, MetaTable
from app.services.metadata_service import MetadataService
from app.core.orm import AsyncSessionLocal
import asyncio

# Setup Test DB Logic
# We use the AsyncSessionLocal from app.core.orm

async def test_metadata_lifecycle():
    async with AsyncSessionLocal() as db:
        dataset_name = "test_dataset_auto"
        
        # Cleanup previous run
        existing = await MetadataService.get_dataset_by_name(db, dataset_name)
        if existing:
            await MetadataService.delete_dataset(db, existing.id)
            
        try:
            # 2. Create Dataset
            print("Creating Dataset...")
            ds_data = {
                "name": dataset_name,
                "display_name": "Test DS",
                "description": "For unit testing",
                "tags": ["test"],
                "data_source": "clickhouse"
            }
            ds = await MetadataService.create_dataset(db, ds_data)
            assert ds.id is not None
            assert ds.name == dataset_name
            
            # 3. Create Table
            print("Creating Table...")
            table_data = {
                "physical_name": "test_table",
                "term": "测试表",
                "description": "Simple test",
                "columns": [
                    {"physical_name": "col1", "term": "列1", "type": "Int"},
                    {"physical_name": "col2", "term": "列2", "enums": [{"value": 1, "label": "A"}]}
                ]
            }
            tbl = await MetadataService.save_table_metadata(db, ds.id, table_data)
            assert tbl.physical_name == "test_table"
            assert len(tbl.columns) == 2
            
            # 4. Export YAML
            print("Exporting YAML...")
            yaml_str = await MetadataService.export_dataset_yaml(db, ds.id)
            print("YAML Output Check:")
            print(yaml_str)
            assert "dataset: test_dataset_auto" in yaml_str
            assert "term: 测试表" in yaml_str
            assert "col1" in yaml_str
            
        finally:
            # 5. Cleanup
            print("Cleaning up...")
            try:
                # Re-fetch because session might have expired if committed
                existing = await MetadataService.get_dataset_by_name(db, dataset_name)
                if existing:
                    await MetadataService.delete_dataset(db, existing.id)
            except Exception as e:
                print(f"Cleanup warning: {e}")

    print("✅ Metadata Lifecycle Test Passed")

if __name__ == "__main__":
    asyncio.run(test_metadata_lifecycle())
