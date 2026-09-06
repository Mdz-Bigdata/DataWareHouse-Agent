from fastapi import APIRouter, Depends
from typing import Dict, Any
from app.core.dependencies import get_current_user
from app.services.config_service import ConfigService
import logging

router = APIRouter()

@router.get("/health")
async def check_llm_config(user: Dict[str, Any] = Depends(get_current_user)):
    """检查 LLM 配置状态"""
    try:
        configs = {}
        llm_keys = ['llm_model_name', 'llm_temperature']
        
        for key in llm_keys:
            value = await ConfigService.get(key)
            configs[key] = {
                'exists': value is not None,
                'value': value
            }
        
        # 尝试创建 LLM 实例 (Using new provider)
        try:
            from app.services.ai.config import AgentConfigProvider
            # Just try to get the configured LLM, which will test DB lookup + client creation
            llm = await AgentConfigProvider.get_configured_llm(streaming=False)
            if llm:
                configs['llm_instance'] = {'status': 'success', 'message': f'LLM ready: {llm.model_name}'}
            else:
                 configs['llm_instance'] = {'status': 'error', 'message': 'LLM factory returned None'}
        except Exception as e:
            configs['llm_instance'] = {'status': 'error', 'message': str(e)}
        
        return configs
    except Exception as e:
        logging.error(f"Health check failed: {str(e)}", exc_info=True)
        return {'error': str(e)}
