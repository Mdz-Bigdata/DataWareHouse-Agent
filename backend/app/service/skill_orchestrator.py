# -*- coding: utf-8 -*-
import logging
from typing import Any, Dict, List, Optional
from app.service.skills.base_skill import BaseSkill, SkillContext, SkillResult
from app.service.skills.attribution_skill import attribution_skill
from app.service.skills.lineage_skill import lineage_skill

logger = logging.getLogger(__name__)

# =====================================================================
# Skill-Hub 顶层技能协调器与路由器 (Skill Orchestrator)
# 对应阿里 QwenPaw-Data (Skill-Hub) 核心设计：
# 统一接入自然语言请求，通过多维度意图感知与置信度打分，
# 动态将用户意图路由调度至最专业的分析技能中执行，实现模块化解耦与可扩展性。
# =====================================================================

class SkillOrchestrator:
    def __init__(self):
        # 注册所有可用专业技能
        self.skills: List[BaseSkill] = [
            attribution_skill,
            lineage_skill
        ]

    def route(self, ctx: SkillContext) -> Optional[BaseSkill]:
        """
        根据用户意图进行技能路由分发
        :return: 匹配得分最高的专业技能，若均未触发则返回 None (默认走指标问数 DSL 流水线)
        """
        best_skill: Optional[BaseSkill] = None
        best_score = 0.0

        for skill in self.skills:
            can_handle, score = skill.can_handle(ctx)
            if can_handle and score > best_score:
                best_score = score
                best_skill = skill

        if best_skill and best_score >= 0.80:
            logger.info(
                "SkillOrchestrator 成功命中专业分析技能: [%s] (置信度得分: %.2f)",
                best_skill.name, best_score
            )
            return best_skill

        # 默认返回 None，由基础问数引擎处理常规统计与聚合
        return None

# 单例导出
skill_orchestrator = SkillOrchestrator()
