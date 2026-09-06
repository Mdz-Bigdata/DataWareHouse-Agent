from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_publish_generates_private_artifact_and_resolves_matching_token(tmp_path, monkeypatch):
    from app.services.ai.tools import generated_file_service

    monkeypatch.setattr(generated_file_service, "generated_files_root", lambda: tmp_path)
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"workbook")

    artifact = generated_file_service.publish(source, "report.xlsx")

    assert artifact.download_url.startswith("/api/v1/chat/generated-files/")
    assert "static/uploads" not in artifact.download_url
    resolved = generated_file_service.resolve_for_download(
        artifact.artifact_id,
        artifact.token,
    )
    assert resolved is not None
    assert resolved.path.read_bytes() == b"workbook"


def test_resolve_for_download_rejects_wrong_token(tmp_path, monkeypatch):
    from app.services.ai.tools import generated_file_service

    monkeypatch.setattr(generated_file_service, "generated_files_root", lambda: tmp_path)
    source = tmp_path / "source.docx"
    source.write_bytes(b"document")
    artifact = generated_file_service.publish(source, "letter.docx")

    assert generated_file_service.resolve_for_download(artifact.artifact_id, "wrong") is None
