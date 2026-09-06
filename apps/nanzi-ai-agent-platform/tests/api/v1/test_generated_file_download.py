from httpx import ASGITransport, AsyncClient
import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_generated_file_download_uses_capability_token(tmp_path, monkeypatch):
    from app.main import app
    from app.services.ai.tools import generated_file_service

    monkeypatch.setattr(generated_file_service, "generated_files_root", lambda: tmp_path)
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"workbook")
    artifact = generated_file_service.publish(source, "report.xlsx")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(artifact.download_url)

    assert response.status_code == 200
    assert response.content == b"workbook"
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
