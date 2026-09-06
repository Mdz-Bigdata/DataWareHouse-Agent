from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.entities.business_rule import BusinessRuleRevision
from app.models.mysql.business_rule_mysql import BusinessRuleRevisionMySQL
from app.repositories.mysql.business_rule_repository import (
    BusinessRuleRepository,
    business_rule_to_entity,
)

RULE_TYPES = {
    "display_convention",
    "filter_requirement",
    "join_requirement",
    "metric_constraint",
    "time_interpretation",
}
RULE_INTENTS = {"aggregate", "compare", "detail", "ranking", "trend"}
_RULE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SEMANTIC_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.:-]{1,160}$")
_FORBIDDEN_PATTERNS = (
    re.compile(r"(?is)\b(ignore|disregard|forget)\b.{0,50}\b(previous|system|developer|instructions?)\b"),
    re.compile(r"(?i)\b(system|developer|assistant|user)\s*:"),
    re.compile(r"(?i)<\|/?(?:system|developer|assistant|user)[^>]*\|>"),
    re.compile(r"(?i)\[/?INST\]"),
    re.compile(r"```|--|/\*|\*/|;"),
    re.compile(r"(?i)\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|GRANT|REVOKE|UNION)\b"),
    re.compile(r"(忽略|无视|绕过).{0,24}(指令|规则|限制|提示)"),
    re.compile(r"(系统|开发者|助手|用户)\s*(提示|消息|角色)\s*[:：]"),
    re.compile(r"(你现在是|请扮演|改为输出|只输出|泄露提示词|显示提示词)"),
)


@dataclass(frozen=True)
class ApplicableBusinessRules:
    rules: list[BusinessRuleRevision]
    semantic_release_id: str | None = None
    semantic_release_version: int | None = None
    business_rule_set_id: str | None = None
    business_rule_set_version: int | None = None


class BusinessRuleService:
    def __init__(self, repository: BusinessRuleRepository):
        self.repository = repository

    async def create_draft(
        self,
        *,
        rule_key: str,
        rule_type: str,
        content: str,
        domain: str,
        datasource: str,
        intents: list[str],
        semantic_ids: list[str],
        priority: int,
        created_by: str,
    ) -> BusinessRuleRevision:
        normalized_content = unicodedata.normalize("NFKC", content)
        _validate_rule(
            rule_key=rule_key,
            rule_type=rule_type,
            content=normalized_content,
            domain=domain,
            datasource=datasource,
            intents=intents,
            semantic_ids=semantic_ids,
            priority=priority,
        )
        version = await self.repository.next_version(
            rule_key=rule_key,
            domain=domain,
            datasource=datasource,
        )
        row = BusinessRuleRevisionMySQL(
            id=str(uuid.uuid4()),
            rule_key=rule_key,
            version=version,
            rule_type=rule_type,
            content=normalized_content.strip(),
            domain=domain.strip(),
            datasource=datasource.strip(),
            intents=_dedupe(intents),
            semantic_ids=_dedupe(semantic_ids),
            priority=priority,
            status="draft",
            created_by=created_by,
        )
        await self.repository.add(row)
        await self.repository.session.commit()
        return business_rule_to_entity(row)

    async def review(
        self,
        rule_id: str,
        *,
        reviewer_id: str,
        approved: bool,
    ) -> BusinessRuleRevision:
        row = await self._get(rule_id)
        if row.status != "draft":
            raise ValueError("只有草稿规则可以审核")
        row.status = "reviewed" if approved else "disabled"
        row.reviewer_id = reviewer_id
        row.reviewed_at = datetime.now()
        await self.repository.session.commit()
        return business_rule_to_entity(row)

    async def publish(self, rule_id: str) -> BusinessRuleRevision:
        row = await self._get(rule_id)
        if row.status != "reviewed":
            raise ValueError("只有已审核规则可以发布")
        await self.repository.disable_other_published_revisions(row)
        row.status = "published"
        row.published_at = datetime.now()
        await self.repository.session.commit()
        return business_rule_to_entity(row)

    async def disable(self, rule_id: str) -> BusinessRuleRevision:
        row = await self._get(rule_id)
        row.status = "disabled"
        await self.repository.session.commit()
        return business_rule_to_entity(row)

    async def list_applicable(
        self,
        *,
        domain: str,
        datasource: str,
        intent: str,
        semantic_ids: set[str],
    ) -> list[BusinessRuleRevision]:
        resolution = await self.resolve_applicable(
            domain=domain,
            datasource=datasource,
            intent=intent,
            semantic_ids=semantic_ids,
        )
        return resolution.rules

    async def resolve_applicable(
        self,
        *,
        domain: str,
        datasource: str,
        intent: str,
        semantic_ids: set[str],
    ) -> ApplicableBusinessRules:
        resolver = getattr(self.repository, "list_effective_for_scope", None)
        if resolver is None:
            rows = await self.repository.list_for_scope(
                domain=domain,
                datasource=datasource,
                status="published",
            )
            release = None
            rule_set = None
        else:
            rows, release, rule_set = await resolver(
                domain=domain,
                datasource=datasource,
            )
        result: list[BusinessRuleRevision] = []
        for row in rows:
            intents = set(row.intents or [])
            bound_ids = set(row.semantic_ids or [])
            if intents and intent not in intents:
                continue
            if bound_ids and not bound_ids.intersection(semantic_ids):
                continue
            result.append(business_rule_to_entity(row))
        return ApplicableBusinessRules(
            rules=result,
            semantic_release_id=release.id if release is not None else None,
            semantic_release_version=release.version if release is not None else None,
            business_rule_set_id=rule_set.id if rule_set is not None else None,
            business_rule_set_version=(rule_set.version if rule_set is not None else None),
        )

    async def _get(self, rule_id: str) -> BusinessRuleRevisionMySQL:
        row = await self.repository.get(rule_id)
        if row is None:
            raise LookupError("业务规则不存在")
        return row


def _validate_rule(
    *,
    rule_key: str,
    rule_type: str,
    content: str,
    domain: str,
    datasource: str,
    intents: list[str],
    semantic_ids: list[str],
    priority: int,
) -> None:
    if not _RULE_KEY_PATTERN.fullmatch(rule_key):
        raise ValueError("规则编码必须使用小写字母、数字和下划线")
    if rule_type not in RULE_TYPES:
        raise ValueError("业务规则类型无效")
    if not content.strip() or len(content) > 1000:
        raise ValueError("业务规则内容不能为空且不能超过 1000 个字符")
    if any(pattern.search(content) for pattern in _FORBIDDEN_PATTERNS):
        raise ValueError("业务规则内容疑似包含提示词注入或原始 SQL")
    if any(
        (ord(char) < 32 or unicodedata.category(char) in {"Cc", "Cf"})
        and char not in "\n\t"
        for char in content
    ):
        raise ValueError("业务规则内容包含非法控制字符")
    if not domain.strip() or not datasource.strip():
        raise ValueError("业务规则必须指定领域和数据源")
    if any(intent not in RULE_INTENTS for intent in intents):
        raise ValueError("业务规则意图作用域无效")
    if any(not _SEMANTIC_ID_PATTERN.fullmatch(item) for item in semantic_ids):
        raise ValueError("业务规则语义标识无效")
    if rule_type in {"join_requirement", "metric_constraint"} and not semantic_ids:
        raise ValueError("当前规则类型必须绑定语义标识")
    if not 0 <= priority <= 1000:
        raise ValueError("业务规则优先级必须在 0 到 1000 之间")


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values))
