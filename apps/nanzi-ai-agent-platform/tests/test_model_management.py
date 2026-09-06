import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.main import app
from app.core.orm import get_db_session
from app.models.ai_model import AIModel
from app.core.dependencies import require_admin
import uuid

# Mock Admin User
@pytest.fixture
def admin_headers(admin_api_key):
    return {"X-API-Key": admin_api_key}

@pytest.mark.asyncio
async def test_model_management_flow(client: AsyncClient, admin_headers):
    # 1. Create Model
    new_model = {
        "name": "Test GPT",
        "model_id": "test-gpt-v1",
        "provider": "openai",
        "type": "llm",
        "api_base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "is_active": True
    }
    
    response = await client.post("/api/portal/models", json=new_model, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test GPT"
    assert data["has_api_key"] == True
    model_db_id = data["id"]
    
    # 2. List Models
    response = await client.get("/api/portal/models", headers=admin_headers)
    assert response.status_code == 200
    models = response.json()
    assert len(models) >= 1
    found = next((m for m in models if m["id"] == model_db_id), None)
    assert found is not None
    assert found["model_id"] == "test-gpt-v1"
    
    # 3. Update Model
    update_data = {"name": "Test GPT Updated"}
    response = await client.put(f"/api/portal/models/{model_db_id}", json=update_data, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Test GPT Updated"
    
    # 4. Filter by Type
    response = await client.get("/api/portal/models?type=llm", headers=admin_headers)
    assert response.status_code == 200
    llms = response.json()
    assert all(m["type"] == "llm" for m in llms)
    
    # 5. Delete Model (Soft Delete)
    response = await client.delete(f"/api/portal/models/{model_db_id}", headers=admin_headers)
    assert response.status_code == 200
    
    # Verify it's gone from list (since list filters is_active=True)
    response = await client.get("/api/portal/models", headers=admin_headers)
    models_after = response.json()
    assert not any(m["id"] == model_db_id for m in models_after)

