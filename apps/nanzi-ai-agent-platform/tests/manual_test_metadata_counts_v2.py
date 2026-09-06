import asyncio
from app.services.metadata_service import MetadataService
from app.core.orm import AsyncSessionLocal

async def test_metadata_counts():
    async with AsyncSessionLocal() as db:
        ds_name = "test_counts_ds"
        
        # Cleanup
        existing = await MetadataService.get_dataset_by_name(db, ds_name)
        if existing:
            await MetadataService.delete_dataset(db, existing.id)

        try:
            # 1. Create Dataset
            print("Creating dataset...")
            ds = await MetadataService.create_dataset(db, {
                "name": ds_name, 
                "display_name": "Count Test",
                "description": "desc"
            })
            
            # 2. Create Tables
            print("Creating tables...")
            t1 = await MetadataService.save_table_metadata(db, ds.id, {
                "physical_name": "t1", "term": "T1", "columns": [{"physical_name": "id", "term": "ID", "type": "int"}]
            })
            t2 = await MetadataService.save_table_metadata(db, ds.id, {
                "physical_name": "t2", "term": "T2", "columns": [{"physical_name": "id", "term": "ID", "type": "int"}]
            })
            
            # 3. Create Metric
            print("Creating metric...")
            await MetadataService.create_metric(db, ds.id, {
                "name": "m1", "display_name": "M1", "calculation_logic": "count(*)"
            })
            
            # 4. Create Relationship
            print("Creating relationship...")
            # We need to use create_relationship via service or directly via model if service method varies
            # Checking service... MetadataService.create_relationship exists
            await MetadataService.create_relationship(db, ds.id, {
                "source_table_id": t1.id,
                "target_table_id": t2.id,
                "join_condition": "t1.id = t2.id",
                "join_type": "LEFT"
            })
            
            # 5. Verify Counts via get_datasets (List View)
            print("Verifying List View Counts...")
            datasets = await MetadataService.get_datasets(db)
            target_ds = next((d for d in datasets if d.id == ds.id), None)
            assert target_ds is not None
            
            print(f"Counts -> Tables: {target_ds.table_count}, Metrics: {target_ds.metric_count}, Rels: {target_ds.relationship_count}")
            assert target_ds.table_count == 2
            assert target_ds.metric_count == 1
            assert target_ds.relationship_count == 1
            
            # 6. Verify Counts via get_dataset_by_id (Detail View)
            print("Verifying Detail View Counts...")
            detail_ds = await MetadataService.get_dataset_by_id(db, ds.id, is_admin=True)
            print(f"Detail Counts -> Tables: {detail_ds.table_count}, Metrics: {detail_ds.metric_count}, Rels: {detail_ds.relationship_count}")
            assert detail_ds.table_count == 2
            assert detail_ds.metric_count == 1
            assert detail_ds.relationship_count == 1
            
            print("✅ ALL COUNT TESTS PASSED")
            
        finally:
            # Cleanup
            print("Cleaning up...")
            existing = await MetadataService.get_dataset_by_name(db, ds_name)
            if existing:
                await MetadataService.delete_dataset(db, existing.id)

if __name__ == "__main__":
    asyncio.run(test_metadata_counts())
