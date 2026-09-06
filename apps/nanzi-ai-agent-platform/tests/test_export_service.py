import pytest
import pandas as pd
from app.services.ai.export_service import ExportService

def test_json_to_csv_normalization():
    # Test case 1: { columns: [], items: [] }
    data = {
        "columns": ["ID", "Name", "Age"],
        "items": [
            [1, "Alice", 25],
            [2, "Bob", 30]
        ]
    }
    csv_out = ExportService.json_to_csv(data)
    assert "ID,Name,Age" in csv_out
    assert "1,Alice,25" in csv_out
    
    # Test case 2: List of dicts
    data_list = [
        {"id": 1, "val": "A"},
        {"id": 2, "val": "B"}
    ]
    csv_out_2 = ExportService.json_to_csv(data_list)
    assert "id,val" in csv_out_2
    assert "1,A" in csv_out_2

def test_json_to_excel_smoke():
    data = {
        "columns": [{"name": "col1"}, {"name": "col2"}],
        "items": [[1, 2], [3, 4]]
    }
    excel_bytes = ExportService.json_to_excel(data)
    assert len(excel_bytes) > 0
    # Verification of xlsx header
    assert excel_bytes.startswith(b"PK\x03\x04")

if __name__ == "__main__":
    pytest.main([__file__])
