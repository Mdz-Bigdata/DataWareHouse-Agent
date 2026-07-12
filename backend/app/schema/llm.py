# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

# NOTE: 定义模型配置管理接口的 Pydantic 校验 Schema。

class VendorConfig(BaseModel):
  api_key: str
  base_url: str
  text_models: List[str]
  multimodal_models: List[str]
  active_text_model: str
  active_multimodal_model: str

class LLMConfigRequest(BaseModel):
  active_vendor: str
  vendors: Dict[str, VendorConfig]

class TestConnectionRequest(BaseModel):
  vendor: str
  api_key: str
  base_url: str

class TestConnectionResponse(BaseModel):
  success: bool
  message: str
  text_models: List[str]
  multimodal_models: List[str]
