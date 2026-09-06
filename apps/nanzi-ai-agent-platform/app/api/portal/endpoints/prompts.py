from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional, Dict, Any

from app.core.dependencies import require_api_key, get_current_user, require_permission
from app.schemas.prompt import (
    PromptMetadata, PromptDetail, PromptSource, 
    PromptTestRequest, PromptTestResponse, PromptSaveRequest,
    PromptOptimizeResponse
)
from app.services.ai.prompt_ops.prompt_service import PromptService

router = APIRouter()

@router.get("/", response_model=List[PromptMetadata])
async def list_prompts(user: Dict[str, Any] = Depends(get_current_user)):
    """获取所有提示词元数据列表 (系统级 +智能体级)"""
    return await PromptService.get_all_prompts()

@router.get("/detail", response_model=PromptDetail)
async def get_prompt_detail(
    source: PromptSource, 
    target_id: str, 
    version: Optional[int] = None,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """获取特定提示词的内容详情"""
    return await PromptService.get_prompt_detail(source, target_id, version)

@router.post("/test", response_model=PromptTestResponse)
async def test_prompt(
    data: PromptTestRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """在 Playground 中测试提示词 (变量注入 + LLM 推理)"""
    try:
        return await PromptService.test_prompt(data.content, data.variables, data.user_input, data.model)
    except Exception as e:
        import logging
        logging.error(f"Prompt test failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Test failed: {str(e)}")

@router.post("/save", dependencies=[Depends(require_permission("element", "element:prompts:edit"))])
async def save_prompt(
    data: PromptSaveRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """保存提示词变更 (具有编辑权限)"""
    changed = await PromptService.save_prompt(
        data.source, 
        data.target_id, 
        data.content, 
        data.version_note, 
        user_name=user.get("user_name", "unknown")
    )
    
    if not changed:
        return {"status": "unchanged", "message": "No changes detected"}
        
    return {"status": "success"}

@router.post("/optimize", response_model=PromptOptimizeResponse, dependencies=[Depends(require_permission("element", "element:prompts:optimize"))])
async def optimize_prompt(
    data: Dict[str, str],
    user: Dict[str, Any] = Depends(get_current_user)
):
    """请求 AI 对提示词进行 8 个维度的优化建议 (具有优化权限)"""
    content = data.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="Missing content")
    
    try:
        return await PromptService.optimize_prompt(content)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"AI 润色结果解析失败，请重试：{e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/history")
async def get_prompt_history(
    source: PromptSource,
    target_id: str,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """获取提示词的变更历史 (System 查 Audit Log, Agent 查 Versions)"""
    if source == PromptSource.SYSTEM_CONFIG:
        # 复用系统配置审计逻辑
        from app.services.config_service import ConfigService
        return await ConfigService.get_config_history(target_id)
    else:
        # Agent 级 Prompts: 将版本记录转换为审计日志格式返回
        return await PromptService.get_agent_prompt_history(target_id)
