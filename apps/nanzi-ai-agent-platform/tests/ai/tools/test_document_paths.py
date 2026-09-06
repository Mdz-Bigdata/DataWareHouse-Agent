from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_resolve_document_input_rejects_unlisted_upload(tmp_path, monkeypatch):
    from app.services.ai.tools import document_paths

    monkeypatch.setattr(document_paths, "get_data_base_dir", lambda: str(tmp_path))
    upload = tmp_path / "uploads" / "other.xlsx"
    upload.parent.mkdir()
    upload.write_bytes(b"x")

    with pytest.raises(document_paths.DocumentPathError, match="当前会话附件"):
        await document_paths.resolve_document_input_path(
            str(upload),
            allowed_attachment_paths=[],
            user_id=7,
            conversation_id="conversation-1",
            allowed_extensions={".xlsx"},
        )


@pytest.mark.asyncio
async def test_resolve_document_input_accepts_listed_upload(tmp_path, monkeypatch):
    from app.services.ai.tools import document_paths

    monkeypatch.setattr(document_paths, "get_data_base_dir", lambda: str(tmp_path))
    upload = tmp_path / "uploads" / "report.xlsx"
    upload.parent.mkdir()
    upload.write_bytes(b"workbook")

    resolved = await document_paths.resolve_document_input_path(
        str(upload),
        allowed_attachment_paths=[str(upload)],
        user_id=7,
        conversation_id="conversation-1",
        allowed_extensions={".xlsx"},
    )

    assert resolved == Path(upload).resolve()
