# Office 文档内置工具设计

**日期：** 2026-06-26
**状态：** 已确认，待实现
**范围：** 为 AgentScope 智能体增加 Excel 与 Word 的受控读取、模板修改、文件生成和下载回传能力。

## 目标

用户上传 `.xlsx` 或 `.docx` 文件后，智能体能够：

- 查看文件结构和有限内容；
- 基于原文件创建副本并按用户要求修改；
- 从零创建新的 Excel 或 Word 文件；
- 将生成结果作为带时效下载链接的附件回传给用户。

首期支持 `.xlsx` 和 `.docx`。`.xls`、`.doc`、宏文件、受密码保护的文件和 PDF 转换均明确不在范围内。

## 现有基础与约束

- `ToolRegistry` 是静态工具到 `RuntimeToolSpec` 的注册入口；运行时权限由 `RuntimeToolSpec.permission_scope` 与确认链路处理。
- 用户上传附件位于 `data/uploads`，当前会话的 AgentScope 工作目录位于 `data/agent_workspaces/<user>/<conversation>`。
- 当前 `/static/uploads` 是公开静态目录，不能用于工具生成的私有文件。
- Excel 可直接使用现有 `openpyxl`；Word 需要增加 `python-docx` 依赖。
- 工具结果会进入主智能体上下文，因此读取返回必须结构化、分页且截断，不能返回完整工作簿或全文。

## 方案选择

### 备选方案

1. 为每个动作建立独立工具，例如 `read_excel`、`write_excel`、`read_word`、`write_word`。参数较短，但工具数量会随格式和操作膨胀，模型更容易误选。
2. 交给 `Bash`/Python 脚本处理 Office 文件。开发量小，但提示词依赖重，难以约束路径、返回值、权限和下载回传。
3. 每种文档类型一个受控工具，以 `action` 区分操作，并共享文件发布服务。模型选择稳定，接口可测试，后续扩展格式或操作不改变注册模式。

采用方案 3。

## 架构

### 模块边界

| 模块 | 职责 |
| --- | --- |
| `app/services/ai/tools/document_paths.py` | 校验输入路径、解析受管附件、生成会话输出路径；不处理 Office 格式。 |
| `app/services/ai/tools/generated_file_service.py` | 发布会话生成文件、保存不可猜测的下载令牌与元数据、校验有效期并提供下载文件。 |
| `app/services/ai/tools/excel_document_tool.py` | 基于 `openpyxl` 实现 Excel 的读取、修改和创建。 |
| `app/services/ai/tools/word_document_tool.py` | 基于 `python-docx` 实现 Word 的读取、修改和创建。 |
| `app/api/v1/endpoints/chat.py` 或同域的文件下载路由 | 通过短期令牌下载生成文件，不暴露磁盘路径。 |
| `app/services/ai/tools/registry.py` | 注册四个运行时工具：`excel_document_read`、`excel_document_write`、`word_document_read`、`word_document_write`。 |

工具实现不直接写入 `data/uploads`，不直接创建静态 URL，也不承担 HTTP 权限校验。

### 文件流

1. 前端上传附件后，现有上传 API 将文件保存到 `data/uploads`，聊天消息向智能体提供其受管路径。
2. 主智能体调用文档工具，工具只接受上传目录或当前会话工作目录中的既有文件。
3. 读取操作返回受限摘要；写入操作先复制原件或创建新文档，再保存到当前会话工作目录。
4. 写入操作调用 `GeneratedFileService.publish`。服务将输出复制到私有生成目录，生成随机 `artifact_id` 和至少 256 位熵的令牌，并记录所有者、会话、显示名称、MIME 类型、文件大小与过期时间。
5. 工具返回结构化结果和 `download_url`。主智能体在最终回复中提供链接与简短变更摘要。
6. 下载路由校验 `artifact_id`、令牌和有效期后以 `FileResponse` 发送文件；路径永不出现在响应、提示词或 URL 中。

生成文件默认 24 小时过期。后台清理任务或每次发布/下载时的惰性清理删除过期文件和元数据。

## 工具契约

实现层保持 Excel 和 Word 两个文档模块；运行时拆成四个工具，以适配现有 `RuntimeToolSpec` 的单一权限范围。工具描述必须清晰说明：读取用来了解文件结构；每个写入动作都会生成新文件，不会覆盖用户上传的原文件。

| 工具名 | 允许 action | 权限 |
| --- | --- | --- |
| `excel_document_read` | `inspect`、`read_range` | `read` |
| `excel_document_write` | `create`、`write_cells`、`append_rows`、`create_sheet` | `ask` |
| `word_document_read` | `inspect`、`read_content` | `read` |
| `word_document_write` | `create`、`replace_text`、`append_paragraphs`、`append_table` | `ask` |

工具仍采用 `action` 枚举，但每个工具只接受表中列出的动作；未知或越权 action 直接失败。

### Excel 工具

| action | 必填参数 | 返回内容 |
| --- | --- | --- |
| `inspect` | `path` | 工作表名、维度、合并单元格计数、前 N 行预览。 |
| `read_range` | `path`、`sheet_name`、`range` | 指定范围的二维值数组、截断标识。 |
| `create` | `output_filename`、可选 `sheets` | 新建工作簿并发布。 |
| `write_cells` | `path`、`sheet_name`、`cells`、`output_filename` | 单元格值或公式写入后的发布信息。 |
| `append_rows` | `path`、`sheet_name`、`rows`、`output_filename` | 追加行后的发布信息。 |
| `create_sheet` | `path`、`sheet_name`、`output_filename` | 新工作表后的发布信息。 |

`cells` 使用 `{ "address": "B2", "value": ... }`；公式只能以 `=` 开头并由 `openpyxl` 原样保存。首期保留模板原有样式和公式，不承诺自动复制新增行的样式。

### Word 工具

| action | 必填参数 | 返回内容 |
| --- | --- | --- |
| `inspect` | `path` | 段落数、表格数、标题级别统计和文本预览。 |
| `read_content` | `path`、可选 `start`、`limit` | 段落分页文本、表格摘要、截断标识。 |
| `create` | `output_filename`、可选 `title`、`paragraphs` | 新文档后的发布信息。 |
| `replace_text` | `path`、`replacements`、`output_filename` | 替换计数和发布信息。 |
| `append_paragraphs` | `path`、`paragraphs`、`output_filename` | 新增段落计数和发布信息。 |
| `append_table` | `path`、`headers`、`rows`、`output_filename` | 新表格行列信息和发布信息。 |

`replace_text` 首期仅保证段落内的精确文本替换；跨 run 或跨段落的替换会明确报告为未匹配，不做可能破坏样式的重排。

### 共同返回结构

读取操作：

```json
{
  "status": "ok",
  "summary": "...",
  "data": {},
  "truncated": false
}
```

写入操作：

```json
{
  "status": "ok",
  "summary": "已生成修改后的文件。",
  "changes": {"written_cells": 3},
  "artifact": {
    "filename": "销售汇总_已更新.xlsx",
    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "size": 12345,
    "download_url": "/api/v1/chat/generated-files/<artifact_id>?token=<token>"
  }
}
```

错误返回应说明下一步，例如文件类型不支持、工作表不存在、单元格范围非法、路径不属于当前会话，且不得泄露受限路径或令牌。

## 安全与权限

- 输入路径先经过真实路径归一化，再校验它位于 `data/uploads` 或当前用户当前会话的工作目录；禁止符号链接越界。
- 写入输出只允许相对文件名；移除目录段、控制字符和危险扩展名，服务决定最终文件名。
- 输入文件、输出文件均限制 20MB；Excel 读取采用只读模式，读取行数、列数、单元格数和文本长度设上限。
- Office 文档不能作为任意压缩包解析；库解析错误统一转换为可理解的工具错误。
- `excel_document_read`、`word_document_read` 的运行时权限为 `read`。
- `excel_document_write`、`word_document_write` 的运行时权限为 `ask`，由现有确认流决定是否执行。
- 生成文件下载令牌为短期能力令牌，不使用公开静态目录；令牌过期或文件被清理后返回 404，不区分“令牌错误”和“文件不存在”。

## 提示词与用户体验

- 仅当 Agent 配置中绑定了相应工具，提示词才出现工具能力说明；希望“读后修改”的 Agent 必须绑定同一格式的 read 与 write 两个工具。
- 用户上传 `.xlsx`/`.docx` 后，模型应先 `inspect` 再决定修改范围；当用户明确给出范围或模板结构已知时可直接写入。
- 工具返回下载 URL 后，主智能体必须在最终答复中给出 Markdown 下载链接和变更摘要，不复制文档全文。
- 前端现有 Markdown 链接渲染可先承载下载链接；无须为首期增加文档在线编辑器或预览器。

## 依赖与迁移

- 在 `requirements.txt` 增加 `python-docx>=1.1.0`。
- 不增加数据库迁移。首期生成文件元数据以私有 JSON 清单或现有可用缓存存储，包含过期时间；其生命周期与文件一致。
- 不改变现有上传接口或 `/static/uploads` 行为，避免影响图片和历史附件。

## 验收与测试

### 单元测试

- Excel：创建、读取范围、写单元格、追加行、创建工作表、公式保留、错误工作表/范围。
- Word：创建、段落分页读取、文本替换、追加段落、追加表格、跨 run 替换不破坏原文件。
- 文件边界：路径越界、符号链接越界、非法文件名、错误扩展名、超限文件和损坏文档。
- 发布服务：生成令牌、过期、错误令牌、文件不在私有发布目录、下载文件名和 MIME。
- 注册与权限：四个工具可由 `ToolRegistry.get_runtime_tool` 获得；read 工具为 `read`，write 工具为 `ask`。

### 集成测试

- 上传 `.xlsx` 模板，调用写入动作，断言原文件哈希未变、输出文件可用、下载路由返回正确内容。
- 上传 `.docx` 模板，替换和追加内容后下载，使用 `python-docx` 再次读取并断言内容。
- 不同用户或不同会话不能把对方的工作目录作为输入；过期链接不能下载。
- 验证现有图片上传、静态上传链接和 AgentScope 文件工作区测试仍然通过。

## 非目标

- Excel 图表、透视表、条件格式自动生成与 VBA 宏执行。
- Word 页眉页脚、批注、修订、复杂跨 run 样式保持和 PDF 转换。
- 文件协作编辑、版本历史、长期文档资产管理与在线预览。
