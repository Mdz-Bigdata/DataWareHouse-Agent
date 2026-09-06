import logging
import json
from app.services.ai.tools.tool_compat import tool
from typing import Any, Optional, List
from app.services.ai.ragflow_client import RagFlowClient
from app.services.config_service import ConfigService
from app.services.metadata_rag_service import MetadataRagService

logger = logging.getLogger(__name__)

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.ai.knowledge_utils import (
    normalize_dataset_ids,
    resolve_knowledge_dataset_ids,
    resolve_rag_retrieval_params,
    is_knowledge_base_enabled,
    KNOWLEDGE_BASE_DISABLED_TOOL_ERROR,
)
from app.services.permission_service import PermissionService


@tool
async def search_knowledge_base(query: str, dataset_ids: Optional[str] = None) -> str:
    """
    Search for documents, manuals, and regulations in the Knowledge Base (RAGFlow).

    Args:
        query: The search keywords or question.
        dataset_ids: (Optional) One or more RAGFlow dataset IDs (32-char hex each).
            Preferred formats:
            - Single plain ID: 4525d66cec7111f0a3d00242ac120006
            - Multiple IDs (MUST use single-quoted Python list):
              ['id1','id2']  — correct
              Do NOT use double-quoted JSON like ["id1","id2"].
            Comma-separated without brackets also works: id1,id2
            If omitted, uses the agent's configured datasets or system default.
    """
    client = RagFlowClient(config_prefix="knowledge_ragflow")

    if not await is_knowledge_base_enabled():
        logger.warning("[KnowledgeTool] Knowledge base feature is disabled")
        return KNOWLEDGE_BASE_DISABLED_TOOL_ERROR

    logger.info(f"[KnowledgeTool] Called with query='{query}', explicit_ids='{dataset_ids}'")

    if dataset_ids and not normalize_dataset_ids(dataset_ids):
        return (
            "[Tool Error] Invalid dataset_ids. Use a plain 32-char ID, "
            "comma-separated IDs, or a single-quoted list like "
            "['4525d66cec7111f0a3d00242ac120006'] — do not use [\"...\"]."
        )

    target_datasets, dataset_error = await resolve_knowledge_dataset_ids(
        explicit_tool_ids=dataset_ids,
        query=query,
    )
    if dataset_error and not target_datasets:
        logger.warning("[KnowledgeTool] Dataset resolution blocked: %s", dataset_error)
        return dataset_error

    logger.info("[KnowledgeTool] Resolved dataset_ids: %s", target_datasets)

    ctx = get_current_agent_context()
    if ctx and ctx.user_id and not ctx.is_admin:
        user_name = (ctx.user_dimensions or {}).get("user_name")
        async with AsyncSessionLocal() as session:
            perm = PermissionService(session)
            before = list(target_datasets)
            target_datasets = await perm.filter_knowledge_dataset_ids(
                int(ctx.user_id),
                user_name,
                target_datasets,
            )
            denied = [d for d in before if d not in target_datasets]
            if denied:
                logger.warning(
                    "[KnowledgeTool] Removed datasets without permission: %s",
                    denied,
                )
        if not target_datasets:
            return (
                "[Tool Error] No permission to search the requested knowledge base. "
                "You may only use datasets assigned to you or created by yourself."
            )

    sys_threshold = await ConfigService.get("knowledge_ragflow_similarity_threshold")
    sys_weight = await ConfigService.get("knowledge_ragflow_vector_weight")
    sys_top_k = await ConfigService.get("knowledge_ragflow_metadata_top_k")
    threshold, vector_weight, top_k = resolve_rag_retrieval_params(
        system_threshold=sys_threshold,
        system_weight=sys_weight,
        system_top_k=sys_top_k,
    )

    logger.info(
        "[KnowledgeTool] Using params: threshold=%s, vector_weight=%s, top_k=%s",
        threshold,
        vector_weight,
        top_k,
    )

    try:
        chunks = await client.retrieve(
            query,
            target_datasets,
            top_k=top_k,
            similarity_threshold=threshold,
            vector_similarity_weight=vector_weight,
        )

        if not chunks:
            empty_payload = {
                "status": "empty",
                "content": (
                    "【知识库检索结果】未找到与用户问题高度相关的文档片段。\n"
                    "请明确告知用户：当前知识库中暂无足够依据回答该问题，"
                    "不要编造流程、制度或操作步骤；可建议用户换关键词或联系管理员补充文档。"
                ),
                "citations": [],
            }
            return json.dumps(empty_payload, ensure_ascii=False)
            
        # --- [NEW: Process for Inline Citations] ---
        # 1. Assign sequential IDs to chunks for easier LLM referencing
        # 2. Format a clear prompt instruction for the LLM
        formatted_context = "I found the following information in the knowledge base. Please provide a detailed answer based ON THESE DOCUMENTS ONLY. \n"
        formatted_context += "CRITICAL: For every statement you make based on a document, append its reference as [ID:n] at the end of the sentence.\n\n"
        
        for i, chunk in enumerate(chunks):
            ref_id = str(i + 1)
            # Inject the simple ID into the chunk object so frontend can match it later
            chunk["id"] = ref_id
            
            doc_name = chunk.get("doc_name") or chunk.get("document_name") or "Unknown Document"
            content = chunk.get("content", "").strip()
            
            formatted_context += f"--- [ID:{ref_id}] Source: {doc_name} ---\n{content}\n\n"

        # Return a structured JSON string. The Executor will unpack this.
        # 'content' is what the LLM will see.
        # 'citations' is the metadata for the frontend.
        result = {
            "content": formatted_context,
            "citations": chunks
        }
        return json.dumps(result, ensure_ascii=False)
        
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Knowledge Search Failed: {e}", exc_info=True)
        if MetadataRagService._is_service_unavailable(e):
            return MetadataRagService.knowledge_unavailable_hint(err_msg)
        return f"[Tool Error] Failed to search knowledge base: {err_msg}"
