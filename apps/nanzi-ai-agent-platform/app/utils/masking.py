import json
import re
from typing import Any, Dict, List, Union

# 精确匹配的敏感键名
SENSITIVE_KEYS_FULL = {
    "authorization", "auth", "password", "passwd", "pwd", "token", 
    "api_key", "apikey", "secret", "sk", "private_key"
}

# 子串匹配的敏感词（只要键名包含这些词就脱敏）
SENSITIVE_KEYS_SUB = {
    "password", "passwd", "pwd",
    "access_token", "refresh_token",
    "api_key", "apikey",
    "client_secret", "app_secret",
}

def mask_sensitive_data(data: Any) -> Any:
    """
    递归对数据进行脱敏处理。
    支持 dict, list, 以及 JSON 字符串。
    """
    if data is None:
        return None

    # 如果是字符串，尝试解析为 JSON
    if isinstance(data, str):
        data_trimmed = data.strip()
        if (data_trimmed.startswith('{') and data_trimmed.endswith('}')) or \
           (data_trimmed.startswith('[') and data_trimmed.endswith(']')):
            try:
                parsed = json.loads(data)
                masked_parsed = mask_sensitive_data(parsed)
                return json.dumps(masked_parsed, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                return _regex_mask_string(data)
        else:
            return _regex_mask_string(data)

    if isinstance(data, dict):
        # 特殊处理 {"key": "llm_api_key", "value": "..."} 这种配置模式
        if "key" in data and "value" in data and isinstance(data["key"], str):
            key_name = data["key"].lower()
            if key_name in SENSITIVE_KEYS_FULL or any(sk in key_name for sk in SENSITIVE_KEYS_SUB):
                return {**data, "value": "******"}

        new_dict = {}
        for k, v in data.items():
            k_lower = k.lower()
            # 检查键名是否敏感
            is_sensitive = k_lower in SENSITIVE_KEYS_FULL or any(sk in k_lower for sk in SENSITIVE_KEYS_SUB)
            
            if is_sensitive:
                new_dict[k] = "******"
            else:
                new_dict[k] = mask_sensitive_data(v)
        return new_dict

    if isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]

    return data

def _regex_mask_string(text: str) -> str:
    """
    针对非 JSON 字符串（如 query params 或日志文本）进行脱敏。
    """
    if not text:
        return text
        
    # 合并所有可能的敏感词进行正则匹配
    all_keys = SENSITIVE_KEYS_FULL | SENSITIVE_KEYS_SUB
    sorted_keys = sorted(list(all_keys), key=len, reverse=True)
    keys_pattern = "|".join(sorted_keys)
    
    masked_text = text
    
    # 1. 匹配 Query params 或 简单键值对: key=value 或 key:value
    kv_pattern = rf'((?:^|[&? \t])(?:{keys_pattern}))(?:[:=])([^& \t\n\r]+)'
    masked_text = re.sub(kv_pattern, r'\1=******', masked_text, flags=re.IGNORECASE)
    
    # 2. 匹配 JSON 样式的字符串: "key": "value"
    json_like_pattern = rf'("(?i:{keys_pattern})"\s*[:=]\s*")([^"]+)(")'
    masked_text = re.sub(json_like_pattern, r'\1******\3', masked_text, flags=re.IGNORECASE)
    
    return masked_text
