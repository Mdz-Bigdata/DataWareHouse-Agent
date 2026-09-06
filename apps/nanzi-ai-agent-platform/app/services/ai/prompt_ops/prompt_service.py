import json
import re
import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import text
from app.core.orm import AsyncSessionLocal
from app.services.config_service import ConfigService
from app.services.ai.agent_manager import AgentManagerService
from app.schemas.prompt import PromptMetadata, PromptSource, PromptVersionSummary, PromptDetail, PromptTestResponse
from app.core.llm.client import get_llm_async
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage

logger = logging.getLogger(__name__)

_OPTIMIZE_XML_ITEM_RE = re.compile(
    r"<item>\s*"
    r"<title>(?P<title>.*?)</title>\s*"
    r"<reason>(?P<reason>.*?)</reason>\s*"
    r"<content>\s*(?:<!\[CDATA\[(?P<cdata>.*?)\]\]>|(?P<plain>.*?))\s*</content>\s*"
    r"</item>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_markdown_fence(text: str) -> str:
    clean = str(text or "").strip()
    if not clean.startswith("```"):
        return clean
    lines = clean.splitlines()
    if not lines:
        return clean
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


def _relax_json_text(text: str) -> str:
    """Common LLM JSON cleanups that do not alter string semantics."""
    cleaned = str(text or "").strip().lstrip("\ufeff")
    # Remove trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def _escape_control_chars_in_json_strings(text: str) -> str:
    """Escape raw newlines/tabs inside JSON string literals."""
    out: List[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = False
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out)


def _extract_json_object(text: str) -> Optional[str]:
    cleaned = _strip_markdown_fence(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    return cleaned[start : end + 1]


def _normalize_optimize_payload(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("优化结果不是 JSON 对象")
    raw_items = data.get("suggestions")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("优化结果缺少 suggestions")
    suggestions: List[Dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not content:
            continue
        suggestions.append(
            {
                "title": title or f"优化方案 {len(suggestions) + 1}",
                "content": content,
                "reason": reason or "未提供推荐理由",
            }
        )
    if not suggestions:
        raise ValueError("优化结果没有可用方案")
    return {"suggestions": suggestions}


def parse_optimize_prompt_response(raw_text: str) -> Dict[str, Any]:
    """
    Parse LLM optimize output.

    Prefer XML+CDATA (avoids quote escaping issues in long prompts).
    Fall back to JSON with light repairs for trailing commas / raw newlines.
    """
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("模型返回为空")

    xml_items = list(_OPTIMIZE_XML_ITEM_RE.finditer(text))
    if xml_items:
        suggestions = []
        for match in xml_items:
            content = (match.group("cdata") if match.group("cdata") is not None else match.group("plain") or "")
            content = content.strip()
            if not content:
                continue
            suggestions.append(
                {
                    "title": (match.group("title") or "").strip() or f"优化方案 {len(suggestions) + 1}",
                    "content": content,
                    "reason": (match.group("reason") or "").strip() or "未提供推荐理由",
                }
            )
        if suggestions:
            return {"suggestions": suggestions}

    blob = _extract_json_object(text)
    if not blob:
        raise ValueError("无法从模型输出中提取结构化结果")

    candidates = [
        blob,
        _relax_json_text(blob),
        _escape_control_chars_in_json_strings(_relax_json_text(blob)),
    ]
    last_err: Optional[Exception] = None
    for candidate in candidates:
        try:
            return _normalize_optimize_payload(json.loads(candidate))
        except Exception as exc:  # noqa: BLE001 - try next repair strategy
            last_err = exc
            continue
    raise ValueError(f"模型返回的 JSON 无法解析: {last_err}")

# 定义系统级 Prompt 的映射注册表
# 注意：路由相关的两层系统提示词均已内置到代码，不再通过数据库配置管理
# （避免运营在配置页误改导致路由/意图识别失准）：
#   - router_system_prompt   -> RouterService.DEFAULT_SYSTEM_PROMPT（第一层智能体路由）
#   - intent_recognition_prompt -> IntentService.DEFAULT_SYSTEM_PROMPT（第二层意图识别）
SYSTEM_PROMPT_REGISTRY = {}

class PromptService:
    @staticmethod
    def extract_variables(text: str) -> List[str]:
        """从提示词中提取 {variable} 格式的占位符"""
        return list(set(re.findall(r'\{([^{}]+)\}', text)))

    @staticmethod
    async def get_all_prompts() -> List[PromptMetadata]:
        prompts = []
        
        # 1. 获取系统级 Prompts
        for key, info in SYSTEM_PROMPT_REGISTRY.items():
            val = await ConfigService.get(key)
            if val is not None:
                # 获取历史记录以计算版本
                history = await ConfigService.get_config_history(key)
                
                # 构建版本列表
                versions = []
                total_versions = len(history) + 1
                
                # 最新版本 (当前值)
                latest_update = history[0]["created_at"] if history else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                versions.append(PromptVersionSummary(
                    version_number=total_versions,
                    status="PUBLISHED",
                    comment="当前活跃版本",
                    updated_at=latest_update
                ))
                
                # 历史版本
                for i, h in enumerate(history):
                    v_num = total_versions - (i + 1)
                    if v_num < 1: break
                    versions.append(PromptVersionSummary(
                        version_number=v_num,
                        status="ARCHIVED",
                        comment=h["description"] or "历史快照",
                        updated_at=h["created_at"]
                    ))

                prompts.append(PromptMetadata(
                    id=key,
                    name=info["name"],
                    source=PromptSource.SYSTEM_CONFIG,
                    category=info["category"],
                    description=info["description"],
                    target_key=key,
                    versions=versions
                ))

        # 2. 获取智能体级 Prompts
        async with AsyncSessionLocal() as session:
            # 获取所有活跃智能体
            result = await session.execute(text("SELECT id, name, display_name, description, created_by, is_system FROM ai_agents"))
            agents = result.fetchall()
            
            for agent in agents:
                agent_id, name, display_name, desc, created_by, is_system = agent
                
                # 获取该智能体的版本列表
                v_res = await session.execute(
                    text("SELECT version_number, status, comment, created_at FROM ai_agent_versions WHERE agent_id = :agent_id ORDER BY version_number DESC"),
                    {"agent_id": agent_id}
                )
                v_rows = v_res.fetchall()
                versions = [
                    PromptVersionSummary(
                        version_number=r[0],
                        status=r[1],
                        comment=r[2],
                        updated_at=r[3].strftime("%Y-%m-%d %H:%M:%S") if r[3] else ""
                    ) for r in v_rows
                ]
                
                prompts.append(PromptMetadata(
                    id=f"agent_{agent_id}",
                    name=name,
                    display_name=display_name,
                    source=PromptSource.AGENT,
                    category="Agent",
                    description=desc,
                    agent_id=agent_id,
                    versions=versions,
                    created_by=created_by,
                    is_system=is_system or False
                ))
                    
        return prompts

    @staticmethod
    async def get_prompt_detail(source: PromptSource, target_id: str, version: Optional[int] = None) -> PromptDetail:
        content = ""
        v_num = version
        note = ""

        if source == PromptSource.SYSTEM_CONFIG:
            history = await ConfigService.get_config_history(target_id)
            total_versions = len(history) + 1
            
            if version:
                if version == total_versions:
                    content = await ConfigService.get(target_id, "")
                    # Try to get latest note from most recent history if available
                    if history:
                         note = history[0].get("description", "")
                else:
                    # 获取对应版本的旧值
                    idx = total_versions - version - 1
                    if 0 <= idx < len(history):
                        content = history[idx]["old_value"]
                        note = history[idx].get("description", "")
                    else:
                        content = ""
            else:
                content = await ConfigService.get(target_id, "")
                v_num = total_versions
                if history:
                     note = history[0].get("description", "")
                     
        elif source == PromptSource.AGENT:
            agent_id = target_id.replace("agent_", "")
            async with AsyncSessionLocal() as session:
                if version:
                    result = await session.execute(
                        text("SELECT system_prompt, comment FROM ai_agent_versions WHERE agent_id = :agent_id AND version_number = :version"),
                        {"agent_id": agent_id, "version": version}
                    )
                else:
                    # Fetch Published (Latest Active)
                    result = await session.execute(
                        text("SELECT system_prompt, version_number, comment FROM ai_agent_versions WHERE agent_id = :agent_id AND status = 'PUBLISHED' LIMIT 1"),
                        {"agent_id": agent_id}
                    )
                    # Fallback: If no published, get latest Draft
                    if not result.rowcount:
                         result = await session.execute(
                            text("SELECT system_prompt, version_number, comment FROM ai_agent_versions WHERE agent_id = :agent_id ORDER BY version_number DESC LIMIT 1"),
                            {"agent_id": agent_id}
                        )
                        
                row = result.fetchone()
                if row:
                    content = row[0]
                    if version:
                        note = row[1]
                    else:
                        v_num = row[1]
                        note = row[2]
        
        return PromptDetail(
            id=target_id,
            source=source,
            content=content,
            version_number=v_num,
            version_note=note,
            variables=PromptService.extract_variables(content)
        )

    @staticmethod
    async def test_prompt(content: str, variables: Dict[str, Any], user_input: Optional[str] = None, model_name: Optional[str] = None) -> PromptTestResponse:
        start_time = time.time()
        
        # 1. 插值 (Interpolation)
        # Using .replace() instead of .format() to avoid KeyError when prompt contains JSON braces
        interpolated = content
        for k, v in variables.items():
            interpolated = interpolated.replace(f"{{{k}}}", str(v))
        
        # 2. 调用 LLM
        try:
            llm = await get_llm_async(model=model_name)
            if not llm:
                raise Exception("LLM not configured properly")
            chat_client = chat_client_from_handle(llm)

            # Use user_input as user message if provided
            if user_input:
                messages = [
                    RuntimeMessage(
                        role="system",
                        content=[RuntimeContentBlock(type="text", text=interpolated)],
                    ),
                    RuntimeMessage(
                        role="user",
                        content=[RuntimeContentBlock(type="text", text=user_input)],
                    ),
                ]
            else:
                messages = [
                    RuntimeMessage(
                        role="user",
                        content=[RuntimeContentBlock(type="text", text=interpolated)],
                    )
                ]
            raw_output = await chat_client.generate_text(messages)
            
            latency = (time.time() - start_time) * 1000
            
            return PromptTestResponse(
                raw_output=raw_output,
                interpolated_prompt=interpolated,
                latency_ms=latency
            )
        except Exception as e:
            logger.error(f"LLM invocation failed: {str(e)}", exc_info=True)
            # 返回一个模拟的响应，而不是抛出异常
            return PromptTestResponse(
                raw_output=f"[LLM Error] {str(e)}\n\nInterpolated Prompt:\n{interpolated}",
                interpolated_prompt=interpolated,
                latency_ms=(time.time() - start_time) * 1000
            )

    @staticmethod
    async def save_prompt(source: PromptSource, target_id: str, content: str, version_note: str = "", user_name: str = "system") -> bool:
        if source == PromptSource.SYSTEM_CONFIG:
            return await ConfigService.set_config(
                target_id, 
                content, 
                description=None, 
                changed_by=user_name,
                change_reason=version_note
            )
        elif source == PromptSource.AGENT:
            agent_id = target_id.replace("agent_", "")
            # 创建新版本或更新草稿？
            # 策略：如果当前有 DRAFT，更新它；否则创建一个新的版本号
            async with AsyncSessionLocal() as session:
                try:
                    # 检查是否有 DRAFT
                    result = await session.execute(
                        text("SELECT id, version_number, system_prompt, comment FROM ai_agent_versions WHERE agent_id = :agent_id AND status = 'DRAFT' LIMIT 1"),
                        {"agent_id": agent_id}
                    )
                    draft = result.fetchone()
                    
                    if draft:
                        # Check if content actually changed
                        if draft[2] == content and draft[3] == version_note:
                            return False # No change

                        # Update existing DRAFT
                        await session.execute(
                            text("UPDATE ai_agent_versions SET system_prompt = :content, comment = :note WHERE id = :id"),
                            {"content": content, "note": version_note, "id": draft[0]}
                        )
                        await session.commit()
                        return True
                    else:
                        # 获取最大版本号
                        res_max = await session.execute(
                            text("SELECT MAX(version_number) FROM ai_agent_versions WHERE agent_id = :agent_id"),
                            {"agent_id": agent_id}
                        )
                        max_v = res_max.fetchone()
                        next_v = (max_v[0] or 0) + 1
                        
                        # 获取已发布版本的模型配置作为模板
                        res_tmpl = await session.execute(
                            text("SELECT model_name, tools, synthesis_model_name, synthesis_temperature FROM ai_agent_versions WHERE agent_id = :agent_id AND status = 'PUBLISHED' LIMIT 1"),
                            {"agent_id": agent_id}
                        )
                        template = res_tmpl.fetchone()
                        model_name = template[0] if template else "gpt-3.5-turbo"
                        tools = template[1] if template else "[]"
                        s_model = template[2] if template else None
                        s_temp = template[3] if template else None
                        
                        # 插入新 DRAFT
                        import uuid
                        await session.execute(
                            text("INSERT INTO ai_agent_versions (id, agent_id, version_number, model_name, synthesis_model_name, synthesis_temperature, system_prompt, tools, status, comment) VALUES (:uuid, :agent_id, :next_v, :model_name, :s_model, :s_temp, :content, :tools, 'DRAFT', :note)"),
                            {
                                "uuid": str(uuid.uuid4()), 
                                "agent_id": agent_id, 
                                "next_v": next_v, 
                                "model_name": model_name, 
                                "s_model": s_model,
                                "s_temp": s_temp,
                                "content": content, 
                                "tools": tools, 
                                "note": version_note
                            }
                        )
                    await session.commit()
                    return True
                except Exception:
                    await session.rollback()
                    raise

    @staticmethod
    async def optimize_prompt(content: str) -> Dict[str, Any]:
        """利用 LLM 生成 8 个优化后的提示词版本（含高级范式）。"""
        system_prompt = """你是一个专业的提示词工程专家 (Prompt Engineer)。
你的任务是根据用户提供的原始提示词，生成恰好 8 个不同侧重点的优化版本。
请尽量保留原始提示词中的业务意图与领域约束，在其基础上增强，而不是改写成无关内容。

必须覆盖以下 8 个维度（每个维度 1 个方案，title 需体现维度名称）：
1. **结构化增强**：使用 Markdown 标题、列表、XML 标签等增强语义结构。
2. **少样本学习 (Few-Shot)**：增加具有代表性的示例以引导输出。
3. **角色设定 (Roleplay)**：强化其专家身份和响应风格。
4. **思维链 (CoT)**：引导提示词按逻辑步骤思考。
5. **清晰约束**：明确负面约束和输出格式；必要时补充拒答/脱敏等安全边界。
6. **ReAct / 工具调用规范**：明确何时调用工具、如何解读工具结果、失败时如何降级与说明。
7. **反幻觉 / 取证门禁**：无证据不编造；要求引用/取证；不确定时如实说明。
8. **输出契约 (Schema)**：固定章节/字段/表格结构，便于 ChatBI、知识库或结构化交付。

输出格式要求（优先使用 XML，避免提示词正文中的引号/换行破坏结构）：
请仅返回如下 XML，不要 Markdown 代码块，不要额外解释：
<suggestions>
  <item>
    <title>版本标题 (如：结构化增强版)</title>
    <reason>推荐理由</reason>
    <content><![CDATA[
完全优化后的提示词内容（可含任意引号、换行、Markdown）
]]></content>
  </item>
  <!-- 必须恰好 8 个 item -->
</suggestions>

若无法输出 XML，也可退回 JSON（content 内双引号必须写成 \\"，换行写成 \\n）：
{"suggestions":[{"title":"...","content":"...","reason":"..."}]}"""

        try:
            llm = await get_llm_async()
            if not llm:
                raise Exception("LLM not configured")

            chat_client = chat_client_from_handle(llm)
            messages = [
                RuntimeMessage(
                    role="system",
                    content=[RuntimeContentBlock(type="text", text=system_prompt)],
                ),
                RuntimeMessage(
                    role="user",
                    content=[RuntimeContentBlock(type="text", text=f"原始提示词如下：\n\n{content}")],
                ),
            ]
            raw_text = await chat_client.generate_text(messages)

            try:
                return parse_optimize_prompt_response(raw_text)
            except ValueError as first_err:
                # 一次纠错重试：专治 content 含未转义引号导致的 JSON 损坏
                logger.warning(
                    "Prompt optimize parse failed, retrying with repair prompt: %s",
                    first_err,
                )
                repair_messages = [
                    RuntimeMessage(
                        role="system",
                        content=[
                            RuntimeContentBlock(
                                type="text",
                                text=(
                                    "你是 JSON/XML 修复器。用户会给你一段损坏的提示词优化结果。"
                                    "请改写为合法 XML（推荐）或合法 JSON，保留全部 8 个方案的 title/reason/content。"
                                    "content 优先放在 CDATA 中。只输出修复后的结果，不要解释。"
                                ),
                            )
                        ],
                    ),
                    RuntimeMessage(
                        role="user",
                        content=[
                            RuntimeContentBlock(
                                type="text",
                                text=(
                                    f"解析错误：{first_err}\n\n"
                                    f"待修复内容：\n{raw_text}"
                                ),
                            )
                        ],
                    ),
                ]
                repaired = await chat_client.generate_text(repair_messages)
                return parse_optimize_prompt_response(repaired)
        except Exception as e:
            logger.error(f"Prompt optimization failed: {str(e)}", exc_info=True)
            raise e

    @staticmethod
    async def get_agent_prompt_history(agent_id: str) -> List[Dict[str, Any]]:
        """获取智能体 Prompt 的变更历史，格式化为类似系统审计日志的结构"""
        target_id = agent_id.replace("agent_", "")
        
        async with AsyncSessionLocal() as session:
            # Fetch all versions ordered by number DESC
            result = await session.execute(
                text("SELECT id, version_number, system_prompt, comment, status, created_at FROM ai_agent_versions WHERE agent_id = :agent_id ORDER BY version_number DESC"),
                {"agent_id": target_id}
            )
            versions = result.fetchall()
            
            history = []
            # Calculate diffs by looking at the next item (which is the previous version)
            for i, v in enumerate(versions):
                current_prompt = v[2]
                prev_prompt = ""
                
                # Try to find previous version
                if i + 1 < len(versions):
                    prev_prompt = versions[i+1][2]
                
                history.append({
                    "id": v[0],
                    "change_type": "PUBLISH" if v[4] in ["PUBLISHED", "ARCHIVED"] else "DRAFT",
                    "changed_by": "System", # Agent versions don't track user yet
                    "created_at": v[5].strftime("%Y-%m-%d %H:%M:%S") if v[5] else "",
                    "description": v[3], # The version note/comment
                    "new_value": current_prompt,
                    "old_value": prev_prompt
                })
                
            return history
