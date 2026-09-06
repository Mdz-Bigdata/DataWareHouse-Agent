from __future__ import annotations

from calendar import monthrange
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AgentExecutionHistory
from app.models.permission import Role, UserRoleRelation
from app.models.quota import QuotaPolicy
from app.models.user import User
from app.schemas.quota import QuotaPolicyResponse, QuotaStatusResponse
from app.services.permission_service import PermissionService


class QuotaService:
    PERIOD_MONTHLY = "monthly"

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _month_window(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
        now = now or datetime.now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = monthrange(now.year, now.month)[1]
        end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
        return start, end

    async def get_monthly_usage(self, username: str) -> int:
        if not username:
            return 0
        start, end = self._month_window()
        stmt = select(
            func.coalesce(func.sum(AgentExecutionHistory.total_tokens), 0)
        ).where(
            AgentExecutionHistory.username == username,
            AgentExecutionHistory.created_at >= start,
            AgentExecutionHistory.created_at <= end,
        )
        return int((await self.db.execute(stmt)).scalar() or 0)

    async def _get_policies_for_scope_ids(
        self, scope_type: str, scope_ids: list[int]
    ) -> dict[int, QuotaPolicy]:
        if not scope_ids:
            return {}
        stmt = select(QuotaPolicy).where(
            QuotaPolicy.scope_type == scope_type,
            QuotaPolicy.period == self.PERIOD_MONTHLY,
            QuotaPolicy.scope_id.in_(scope_ids),
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return {int(row.scope_id): row for row in rows if row.scope_id is not None}

    async def _get_policy(
        self, scope_type: str, scope_id: Optional[int]
    ) -> Optional[QuotaPolicy]:
        stmt = select(QuotaPolicy).where(
            QuotaPolicy.scope_type == scope_type,
            QuotaPolicy.period == self.PERIOD_MONTHLY,
        )
        if scope_id is None:
            stmt = stmt.where(QuotaPolicy.scope_id.is_(None))
        else:
            stmt = stmt.where(QuotaPolicy.scope_id == scope_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _resolve_effective_limit(
        self, user_id: int, username: str
    ) -> Tuple[Optional[int], str, Optional[str]]:
        user = await self.db.get(User, user_id)
        if not user:
            return None, "unlimited", None

        roles = [user.role] if user.role else []
        role_stmt = (
            select(Role.id, Role.name)
            .join(UserRoleRelation, Role.id == UserRoleRelation.role_id)
            .where(UserRoleRelation.user_id == user_id)
        )
        business_roles = (await self.db.execute(role_stmt)).all()
        if PermissionService.is_admin_user(roles):
            return None, "admin_bypass", "系统管理员"

        user_policy = await self._get_policy("user", user_id)
        if user_policy and user_policy.enabled:
            if user_policy.limit_tokens is None:
                return None, "user", "用户专属（不限额）"
            return int(user_policy.limit_tokens), "user", "用户专属"

        role_limits: list[Tuple[int, str]] = []
        has_unlimited_role = False
        role_ids = [int(role_id) for role_id, _ in business_roles]
        role_policies = await self._get_policies_for_scope_ids("role", role_ids)
        for role_id, role_name in business_roles:
            role_policy = role_policies.get(int(role_id))
            if not role_policy or not role_policy.enabled:
                continue
            if role_policy.limit_tokens is None:
                has_unlimited_role = True
                break
            role_limits.append((int(role_policy.limit_tokens), role_name))

        if has_unlimited_role:
            best_role = max(business_roles, key=lambda r: r[1]) if business_roles else None
            label = f"角色：{best_role[1]}" if best_role else "角色模板（不限额）"
            return None, "role", label

        if role_limits:
            limit, name = max(role_limits, key=lambda item: item[0])
            return limit, "role", f"角色：{name}"

        system_policy = await self._get_policy("system", None)
        if system_policy and system_policy.enabled:
            if system_policy.limit_tokens is None:
                return None, "system", "系统默认（不限额）"
            return int(system_policy.limit_tokens), "system", "系统默认"

        return None, "unlimited", None

    def _build_status(
        self,
        *,
        used_tokens: int,
        limit_tokens: Optional[int],
        source: str,
        source_label: Optional[str],
        is_admin_bypass: bool,
        action_on_exceed: str = "block",
        policy_enabled: bool = True,
        now: Optional[datetime] = None,
    ) -> QuotaStatusResponse:
        start, end = self._month_window(now)
        remaining = None
        if limit_tokens is not None:
            remaining = max(limit_tokens - used_tokens, 0)
        return QuotaStatusResponse(
            period=self.PERIOD_MONTHLY,
            period_start=start.strftime("%Y-%m-%d"),
            period_end=end.strftime("%Y-%m-%d"),
            used_tokens=used_tokens,
            limit_tokens=limit_tokens,
            remaining_tokens=remaining,
            source=source,  # type: ignore[arg-type]
            source_label=source_label,
            action_on_exceed=action_on_exceed,
            is_admin_bypass=is_admin_bypass,
            policy_enabled=policy_enabled,
        )

    async def get_user_quota_status(
        self, user_id: int, username: str
    ) -> QuotaStatusResponse:
        used = await self.get_monthly_usage(username)
        limit, source, label = await self._resolve_effective_limit(user_id, username)
        is_admin = source == "admin_bypass"
        return self._build_status(
            used_tokens=used,
            limit_tokens=limit,
            source=source,
            source_label=label,
            is_admin_bypass=is_admin,
        )

    async def check_before_call(self, user_info: dict) -> Optional[str]:
        user_id = user_info.get("user_id")
        username = user_info.get("user_name") or user_info.get("username")
        if not user_id or not username:
            return None

        status = await self.get_user_quota_status(int(user_id), str(username))
        if status.is_admin_bypass or status.limit_tokens is None:
            return None
        if status.used_tokens >= status.limit_tokens:
            return (
                f"本月 Token 额度已用尽（已用 {status.used_tokens:,} / 上限 {status.limit_tokens:,}），"
                "请联系管理员调整额度。"
            )
        return None

    def build_warning_message(self, status: QuotaStatusResponse) -> Optional[str]:
        if status.is_admin_bypass or status.limit_tokens is None:
            return None
        if status.used_tokens >= status.limit_tokens:
            return None
        threshold = int(status.limit_tokens * 0.8)
        if status.used_tokens < threshold:
            return None
        remaining = max(status.limit_tokens - status.used_tokens, 0)
        return (
            f"本月 Token 额度即将用尽（已用 {status.used_tokens:,} / 上限 {status.limit_tokens:,}，"
            f"剩余 {remaining:,}）。"
        )

    async def get_scope_policy(
        self,
        scope_type: str,
        scope_id: Optional[int],
        *,
        preview_user_id: Optional[int] = None,
        preview_username: Optional[str] = None,
    ) -> QuotaPolicyResponse:
        policy = await self._get_policy(scope_type, scope_id)
        effective = None
        if scope_type == "user" and scope_id and preview_username:
            effective = await self.get_user_quota_status(scope_id, preview_username)
        response = QuotaPolicyResponse(
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=bool(policy.enabled) if policy else False,
            limit_tokens=int(policy.limit_tokens) if policy and policy.limit_tokens is not None else None,
            action_on_exceed=policy.action_on_exceed if policy else "block",
            inherit=policy is None,
            effective=effective,
        )
        return response

    async def upsert_scope_policy(
        self,
        scope_type: str,
        scope_id: Optional[int],
        *,
        enabled: bool,
        limit_tokens: Optional[int],
    ) -> QuotaPolicyResponse:
        policy = await self._get_policy(scope_type, scope_id)
        if policy:
            policy.enabled = enabled
            policy.limit_tokens = limit_tokens
            policy.action_on_exceed = "block"
        else:
            policy = QuotaPolicy(
                scope_type=scope_type,
                scope_id=scope_id,
                period=self.PERIOD_MONTHLY,
                enabled=enabled,
                limit_tokens=limit_tokens,
                action_on_exceed="block",
            )
            self.db.add(policy)
        await self.db.commit()
        await self.db.refresh(policy)
        return QuotaPolicyResponse(
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=bool(policy.enabled),
            limit_tokens=int(policy.limit_tokens) if policy.limit_tokens is not None else None,
            action_on_exceed=policy.action_on_exceed,
            inherit=False,
        )

    async def delete_scope_policy(
        self, scope_type: str, scope_id: Optional[int]
    ) -> None:
        policy = await self._get_policy(scope_type, scope_id)
        if policy:
            await self.db.delete(policy)
            await self.db.commit()
