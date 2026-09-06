# Office 文档内置工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 让已配置工具的 Agent 能安全读取、修改或创建 \`.xlsx\`/\`.docx\`，并把生成副本作为 24 小时可下载文件回传给用户。

**Architecture:** 增加受当前请求附件白名单约束的文档路径解析器，以及不公开挂载的生成文件发布服务。Excel/Word 各自保留一个格式实现模块，运行时拆成 read/write 四个 \`RuntimeToolSpec\` 以匹配现有按工具确认的权限模型；写入结果由发布服务产生短期能力链接。

**Tech Stack:** Python 3.11, FastAPI, AgentScope \`RuntimeToolSpec\`, Pydantic, \`openpyxl\`, \`python-docx\`, pytest/httpx。

---

## File Structure

- Modify: \`requirements.txt\` - 增加 \`python-docx\`。
- Modify: \`app/core/context.py\` - 记录本轮可访问附件的绝对路径。
- Modify: \`app/services/ai/context_manager.py\` - 将附件白名单写入 \`AgentContext\`。
- Modify: \`app/services/ai/agent_service.py\` - 从已清洗的当前会话消息中提取附件白名单。
- Create: \`app/services/ai/tools/document_paths.py\` - 单一职责：受管路径、文件名、大小和会话输出路径校验。
- Create: \`app/services/ai/tools/generated_file_service.py\` - 单一职责：私有文件发布、令牌清单、过期清理和下载解析。
- Create: \`app/services/ai/tools/excel_document_tool.py\` - Excel 的读写动作和有限输出。
- Create: \`app/services/ai/tools/word_document_tool.py\` - Word 的读写动作和有限输出。
- Modify: \`app/services/ai/tools/registry.py\` - 注册四个 Office 工具并提供显式 \`RuntimeToolSpec\`。
- Modify: \`app/api/v1/endpoints/chat.py\` - 提供令牌保护的生成文件下载路由。
- Create: \`tests/ai/tools/test_document_paths.py\`, \`tests/ai/tools/test_generated_file_service.py\`, \`tests/ai/tools/test_excel_document_tool.py\`, \`tests/ai/tools/test_word_document_tool.py\`。
- Modify: \`tests/ai/tools/test_registry.py\`, \`tests/services/ai/test_agent_context_manager.py\`, \`tests/api/v1/test_upload_attachments.py\`。

### Task 1: Request Attachment Allowlist And File Path Guard

**Files:**
- Create: \`app/services/ai/tools/document_paths.py\`
- Modify: \`app/core/context.py\`
- Modify: \`app/services/ai/context_manager.py\`
- Modify: \`app/services/ai/agent_service.py\`
- Test: \`tests/ai/tools/test_document_paths.py\`, \`tests/services/ai/test_agent_context_manager.py\`

- [ ] **Step 1: Write the failing tests**

\`\`\`python
@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_resolve_document_input_rejects_unlisted_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(document_paths, "get_data_base_dir", lambda: str(tmp_path))
    upload = tmp_path / "uploads" / "other.xlsx"
    upload.parent.mkdir()
    upload.write_bytes(b"x")

    with pytest.raises(DocumentPathError, match="当前会话附件"):
        await document_paths.resolve_document_input_path(
            str(upload), allowed_attachment_paths=[], user_id=7, conversation_id="c1"
        )


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_setup_context_keeps_authorized_attachment_paths():
    await AgentContextManager.setup_context(
        config=_config(), user_info={"user_id": 1, "role": "user"},
        authorized_attachment_paths=["/app/data/uploads/report.xlsx"],
    )
    assert get_current_agent_context().authorized_attachment_paths == [
        "/app/data/uploads/report.xlsx"
    ]
\`\`\`

- [ ] **Step 2: Run test to verify it fails**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_document_paths.py tests/services/ai/test_agent_context_manager.py -q\`

Expected: import failure for \`document_paths\`, or \`setup_context()\` rejects \`authorized_attachment_paths\`.

- [ ] **Step 3: Write minimal implementation**

\`\`\`python
# app/core/context.py
authorized_attachment_paths: List[str] = Field(default_factory=list)

# app/services/ai/context_manager.py
# Add this parameter immediately after knowledge_dataset_ids in setup_context().
authorized_attachment_paths: Optional[List[str]] = None,

# Add this field to the AgentContext() constructor in setup_context().
authorized_attachment_paths=list(authorized_attachment_paths or []),

# app/services/ai/agent_service.py
def _authorized_attachment_paths(messages: list[dict[str, Any]]) -> list[str]:
    return sorted({
        _attachment_abs_path(file_obj)
        for message in messages if message.get("role") == "user"
        for file_obj in message.get("files") or []
        if file_obj.get("url")
    })
\`\`\`

\`document_paths.py\` defines \`DocumentPathError\`, \`MAX_DOCUMENT_BYTES = 20 * 1024 * 1024\`, \`resolve_document_input_path\`, \`resolve_document_output_path\`, and \`sanitize_output_filename\`. Input paths use \`os.path.realpath\`, accept uploads only when their real path is in this request's allowlist, accept workspace files only under the current user/conversation workdir, reject symlink escapes, verify expected extensions, and enforce the size limit.

- [ ] **Step 4: Run test to verify it passes**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_document_paths.py tests/services/ai/test_agent_context_manager.py -q\`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

\`\`\`bash
git add app/core/context.py app/services/ai/context_manager.py app/services/ai/agent_service.py app/services/ai/tools/document_paths.py tests/ai/tools/test_document_paths.py tests/services/ai/test_agent_context_manager.py
git commit -m "feat(ai): guard office document paths"
\`\`\`

### Task 2: Private Generated-File Publishing And Download

**Files:**
- Create: \`app/services/ai/tools/generated_file_service.py\`
- Modify: \`app/api/v1/endpoints/chat.py\`
- Test: \`tests/ai/tools/test_generated_file_service.py\`, \`tests/api/v1/test_upload_attachments.py\`

- [ ] **Step 1: Write the failing tests**

\`\`\`python
@pytest.mark.no_infrastructure
def test_publish_generates_private_artifact_and_resolves_matching_token(tmp_path, monkeypatch):
    monkeypatch.setattr(generated_file_service, "generated_files_root", lambda: tmp_path)
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"workbook")

    artifact = generated_file_service.publish(source, "report.xlsx")

    assert artifact["download_url"].startswith("/api/v1/chat/generated-files/")
    resolved = generated_file_service.resolve_for_download(
        artifact["artifact_id"], artifact["token"]
    )
    assert resolved.path.read_bytes() == b"workbook"


@pytest.mark.asyncio
async def test_generated_file_download_rejects_wrong_token(client, artifact_id):
    response = await client.get(
        f"/api/v1/chat/generated-files/{artifact_id}?token=wrong"
    )
    assert response.status_code == 404
\`\`\`

- [ ] **Step 2: Run test to verify it fails**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_generated_file_service.py tests/api/v1/test_upload_attachments.py -q\`

Expected: import failure for \`generated_file_service\` and a missing route.

- [ ] **Step 3: Write minimal implementation**

\`\`\`python
@dataclass(frozen=True)
class GeneratedFile:
    artifact_id: str
    path: Path
    filename: str
    mime_type: str
    size: int
    expires_at: datetime

def publish(source_path: str | Path, filename: str,
            *, ttl: timedelta = timedelta(hours=24)) -> dict[str, Any]:
    artifact_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    # Copy source to data/generated_files/<artifact_id>/ and write manifest.json.

@router.get("/generated-files/{artifact_id}")
async def download_generated_file(artifact_id: str, token: str):
    artifact = generated_file_service.resolve_for_download(artifact_id, token)
    if artifact is None:
        raise HTTPException(status_code=404, detail="文件不存在或已过期")
    return FileResponse(artifact.path, media_type=artifact.mime_type, filename=artifact.filename)
\`\`\`

The manifest stores \`token_hash = sha256(token)\`, not the raw token. \`resolve_for_download\` uses \`hmac.compare_digest\`, lazily removes expired artifact directories, and returns \`None\` for missing, malformed, expired, or mismatched requests. \`publish\` returns the raw token only to the current tool result, plus a relative \`download_url\`; generated files never go to \`data/uploads\`.

- [ ] **Step 4: Run test to verify it passes**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_generated_file_service.py tests/api/v1/test_upload_attachments.py -q\`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

\`\`\`bash
git add app/services/ai/tools/generated_file_service.py app/api/v1/endpoints/chat.py tests/ai/tools/test_generated_file_service.py tests/api/v1/test_upload_attachments.py
git commit -m "feat(ai): publish generated office files"
\`\`\`

### Task 3: Excel Read And Write Tools

**Files:**
- Create: \`app/services/ai/tools/excel_document_tool.py\`
- Test: \`tests/ai/tools/test_excel_document_tool.py\`

- [ ] **Step 1: Write the failing tests**

\`\`\`python
@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_excel_read_range_returns_bounded_matrix(workbook_path, context_with_attachment):
    result = await excel_document_read.ainvoke({
        "action": "read_range", "path": str(workbook_path),
        "sheet_name": "Sales", "range": "A1:B2",
    })
    assert result["data"]["values"] == [["Month", "Amount"], ["Jan", 10]]


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_excel_write_cells_creates_downloadable_copy(workbook_path, context_with_attachment):
    result = await excel_document_write.ainvoke({
        "action": "write_cells", "path": str(workbook_path), "sheet_name": "Sales",
        "cells": [{"address": "B2", "value": 42}], "output_filename": "sales_updated.xlsx",
    })
    assert result["changes"]["written_cells"] == 1
    assert result["artifact"]["download_url"]
    assert load_workbook(workbook_path)["Sales"]["B2"].value == 10
\`\`\`

- [ ] **Step 2: Run test to verify it fails**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_excel_document_tool.py -q\`

Expected: import failure for \`excel_document_tool\`.

- [ ] **Step 3: Write minimal implementation**

\`\`\`python
@tool
async def excel_document_read(
    action: Literal["inspect", "read_range"], path: str,
    sheet_name: str | None = None, range: str | None = None,
) -> dict[str, Any]:

@tool
async def excel_document_write(
    action: Literal["create", "write_cells", "append_rows", "create_sheet"],
    output_filename: str, path: str | None = None, sheet_name: str | None = None,
    cells: list[dict[str, Any]] | None = None,
    rows: list[list[Any]] | None = None,
) -> dict[str, Any]:
\`\`\`

Use \`load_workbook(input_path, read_only=True, data_only=False)\` for reads and normal mode only for writes. Cap \`inspect\` preview at 20 rows x 20 columns and \`read_range\` at 200 rows x 50 columns. Validate ranges with \`openpyxl.utils.cell.range_boundaries\`. Every write saves to \`resolve_document_output_path\`, calls \`generated_file_service.publish\`, and returns \`status\`, \`summary\`, \`changes\`, and \`artifact\`.

- [ ] **Step 4: Run test to verify it passes**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_excel_document_tool.py -q\`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

\`\`\`bash
git add app/services/ai/tools/excel_document_tool.py tests/ai/tools/test_excel_document_tool.py
git commit -m "feat(ai): add excel document tools"
\`\`\`

### Task 4: Word Read And Write Tools

**Files:**
- Modify: \`requirements.txt\`
- Create: \`app/services/ai/tools/word_document_tool.py\`
- Test: \`tests/ai/tools/test_word_document_tool.py\`

- [ ] **Step 1: Add the dependency and write failing tests**

\`\`\`text
# requirements.txt
python-docx>=1.1.0
\`\`\`

\`\`\`python
@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_word_replace_text_writes_a_copy(template_path, context_with_attachment):
    result = await word_document_write.ainvoke({
        "action": "replace_text", "path": str(template_path),
        "replacements": [{"find": "{{name}}", "replace": "Alice"}],
        "output_filename": "letter_alice.docx",
    })
    assert result["changes"]["replacements"] == 1
    assert result["artifact"]["download_url"]


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_word_read_content_is_paginated(template_path, context_with_attachment):
    result = await word_document_read.ainvoke({
        "action": "read_content", "path": str(template_path), "start": 1, "limit": 1,
    })
    assert result["data"]["paragraphs"] == ["second paragraph"]
\`\`\`

- [ ] **Step 2: Run test to verify it fails**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_word_document_tool.py -q\`

Expected: dependency/import failure for \`docx\` or \`word_document_tool\`.

- [ ] **Step 3: Write minimal implementation**

\`\`\`python
@tool
async def word_document_read(
    action: Literal["inspect", "read_content"], path: str,
    start: int = 0, limit: int = 20,
) -> dict[str, Any]:

@tool
async def word_document_write(
    action: Literal["create", "replace_text", "append_paragraphs", "append_table"],
    output_filename: str, path: str | None = None,
    replacements: list[dict[str, str]] | None = None,
    paragraphs: list[str] | None = None, headers: list[str] | None = None,
    rows: list[list[str]] | None = None, title: str | None = None,
) -> dict[str, Any]:
\`\`\`

\`read_content\` caps \`limit\` at 50 and returns a table summary rather than full table data. \`replace_text\` traverses \`paragraph.runs\` and replaces exact text inside one run only; it never reconstructs cross-run text. \`append_table\` requires non-empty headers and each row has the header width. Writes save to the session output path and publish through \`generated_file_service\`.

- [ ] **Step 4: Run test to verify it passes**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_word_document_tool.py -q\`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

\`\`\`bash
git add requirements.txt app/services/ai/tools/word_document_tool.py tests/ai/tools/test_word_document_tool.py
git commit -m "feat(ai): add word document tools"
\`\`\`

### Task 5: Registry, Permission, Prompt, And End-To-End Regression

**Files:**
- Modify: \`app/services/ai/tools/registry.py\`
- Modify: \`app/services/ai/agent_prompts.py\`
- Test: \`tests/ai/tools/test_registry.py\`, \`tests/ai/runtime/test_agentscope_tooling.py\`

- [ ] **Step 1: Write the failing tests**

\`\`\`python
@pytest.mark.asyncio
async def test_office_runtime_tools_have_explicit_permissions():
    specs = await ToolRegistry.get_runtime_tools([
        "excel_document_read", "excel_document_write",
        "word_document_read", "word_document_write",
    ])
    assert [spec.permission_scope for spec in specs] == ["read", "ask", "read", "ask"]


@pytest.mark.asyncio
async def test_office_write_tool_requires_confirmation():
    spec = await ToolRegistry.get_runtime_tool("excel_document_write")
    decision = await AgentScopeRuntimeTool(spec).check_permissions({}, None)
    assert decision.behavior == PermissionBehavior.ASK
\`\`\`

- [ ] **Step 2: Run test to verify it fails**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_registry.py tests/ai/runtime/test_agentscope_tooling.py -q\`

Expected: Office tool names are unknown.

- [ ] **Step 3: Write minimal integration**

\`\`\`python
# app/services/ai/tools/registry.py
OFFICE_TOOL_PERMISSION_SCOPES = {
    "excel_document_read": "read",
    "excel_document_write": "ask",
    "word_document_read": "read",
    "word_document_write": "ask",
}
_registry.update({
    "excel_document_read": excel_document_read,
    "excel_document_write": excel_document_write,
    "word_document_read": word_document_read,
    "word_document_write": word_document_write,
})
\`\`\`

In \`get_runtime_tool\`, call \`runtime_tool_spec_from_legacy_tool(tool, source_type="static", permission_scope=OFFICE_TOOL_PERMISSION_SCOPES[name])\` before the generic static fallback. Keep all four out of \`get_system_implicit_tools\`: only explicitly configured Agents receive them. Add a prompt helper gated by configured Office tool names: inspect first, preserve the original attachment, and include the returned Markdown download link in the final response.

- [ ] **Step 4: Run focused and cross-module regression tests**

Run: \`REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_document_paths.py tests/ai/tools/test_generated_file_service.py tests/ai/tools/test_excel_document_tool.py tests/ai/tools/test_word_document_tool.py tests/ai/tools/test_registry.py tests/ai/runtime/test_agentscope_tooling.py tests/services/ai/test_agent_context_manager.py tests/api/v1/test_upload_attachments.py -q\`

Expected: all selected tests pass.

Run: \`venv/bin/python -m py_compile app/core/context.py app/services/ai/context_manager.py app/services/ai/agent_service.py app/services/ai/tools/document_paths.py app/services/ai/tools/generated_file_service.py app/services/ai/tools/excel_document_tool.py app/services/ai/tools/word_document_tool.py app/services/ai/tools/registry.py app/api/v1/endpoints/chat.py\`

Expected: exit code 0.

Run: \`git diff --check\`

Expected: no output.

- [ ] **Step 5: Commit**

\`\`\`bash
git add app/services/ai/tools/registry.py app/services/ai/agent_prompts.py tests/ai/tools/test_registry.py tests/ai/runtime/test_agentscope_tooling.py
git commit -m "feat(ai): register office document tools"
\`\`\`
