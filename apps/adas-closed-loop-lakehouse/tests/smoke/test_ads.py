"""冒烟：ADS 应用数据层——面向报表 / 大屏 / 应用，零 JOIN，开箱即用。

主流程：11 张 ADS 产品表 → 按 workload 选路由 → 查询服务取数（走 StaticRowSource，
不连 StarRocks）→ 网关鉴权 / 限流 / 审计。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse.ads import gateway as G
from adas_lakehouse.ads import materialize as M
from adas_lakehouse.ads import products as P
from adas_lakehouse.ads import query as Q
from adas_lakehouse.ads import routing as R
from adas_lakehouse.ads import schema as S

pytestmark = pytest.mark.smoke

TABLE = "ads_closed_loop_dashboard"


def _rows(table: str, n: int = 3) -> list[dict]:
    columns = S.column_names(table)
    out = []
    for i in range(n):
        row = dict.fromkeys(columns, f"v{i}")
        if "stat_date" in row:
            row["stat_date"] = f"2026-03-0{i + 1}"
        out.append(row)
    return out


def _service(table: str = TABLE) -> Q.AdsQueryService:
    return Q.AdsQueryService(Q.StaticRowSource({table: _rows(table)}))


# --------------------------------------------------------------------------- 产品矩阵


def test_eleven_ads_products_match_the_registry():
    assert len(P.ADS_TABLE_NAMES) == 11
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.domains import Layer

    registered = {t.name for t in registry.by_layer(Layer.ADS)}
    assert set(P.ADS_TABLE_NAMES) == registered


def test_every_product_declares_theme_domain_and_consumers():
    for product in P.PRODUCTS:
        assert product.table and product.theme and product.domain
        assert product.serves, f"{product.table} 没说明主要服务对象"
        assert product.consumed_by, f"{product.table} 没说明哪项闭环服务来读"
        assert product.source_tables, f"{product.table} 没说明它从哪几张表物化"
        assert P.get_product(product.table) is product

    assert len({p.table for p in P.PRODUCTS}) == 11
    assert [p.ordinal for p in P.PRODUCTS] == list(range(1, 12))


def test_products_can_be_sliced_by_theme_platform_and_service():
    covered = set()
    for theme in P.AdsTheme:
        covered |= {p.table for p in P.products_by_theme(theme)}
    assert covered == set(P.ADS_TABLE_NAMES)

    # serves = 主要服务对象（业务平台）；consumed_by = 读它的闭环业务服务
    for platform in P.BusinessPlatform:
        for product in P.products_by_platform(platform):
            assert platform in product.serves

    for service in P.ClosedLoopService:
        for product in P.products_by_service(service):
            assert service in product.consumed_by


def test_product_source_tables_are_all_registered():
    from adas_lakehouse.catalog import registry

    known = {t.name for t in registry.all_tables()}
    for product in P.PRODUCTS:
        for source in product.source_tables:
            assert source in known, f"{product.table} 的上游 {source!r} 未注册"


def test_unknown_product_lookup_fails_loudly():
    from adas_lakehouse.ads.errors import UnknownTableError

    with pytest.raises(UnknownTableError, match="11 张"):
        P.get_product("ads_not_a_product")


def test_matrix_renders():
    assert P.matrix_rows()
    assert P.render_matrix()


# --------------------------------------------------------------------------- Schema 对账


def test_starrocks_schema_agrees_with_the_paimon_catalog():
    """ADS 表在 StarRocks 里的列必须与 Paimon 契约一致，否则物化出来对不上号。"""
    assert S.verify_against_catalog() == {}


def test_every_ads_table_renders_starrocks_ddl():
    ddl = S.render_all_starrocks_ddl()
    for table in P.ADS_TABLE_NAMES:
        assert table in ddl
    assert ddl.upper().count("CREATE TABLE") >= 11


def test_key_columns_use_the_bounded_varchar_length():
    """主键列不能用 65533 长度的 VARCHAR——StarRocks 的 key 列有长度上限。"""
    assert S.KEY_VARCHAR_LENGTH == 128
    assert S.DEFAULT_VARCHAR_LENGTH == 65533

    seen_key = False
    for name in P.ADS_TABLE_NAMES:
        for column in S.starrocks_table(name).columns:
            if "VARCHAR" not in column.type.upper():
                continue
            if column.is_key:
                seen_key = True
                assert f"({S.KEY_VARCHAR_LENGTH})" in column.type, f"{name}.{column.name}"
            else:
                assert f"({S.DEFAULT_VARCHAR_LENGTH})" in column.type, f"{name}.{column.name}"
    assert seen_key, "11 张 ADS 表里一个 VARCHAR 主键列都没有，断言等于没跑"


def test_require_column_rejects_a_typo():
    from adas_lakehouse.ads.errors import UnknownColumnError

    S.require_column(TABLE, "stat_date")
    with pytest.raises(UnknownColumnError):
        S.require_column(TABLE, "stat_dat")


# --------------------------------------------------------------------------- 路由


def test_scalar_workloads_get_a_route_with_a_reason():
    from adas_lakehouse.ads.errors import ServiceUnavailableError

    for workload in (R.WorkloadKind.DASHBOARD_REPORT, R.WorkloadKind.EXPLORATORY_ADHOC):
        decision = R.route_for(workload)
        assert decision.route in set(R.QueryRoute)
        assert decision.workload_cn and decision.reason_cn
        assert decision.latency_expectation_cn

    # 语义检索归 vector 子系统，ADS 服务层只做标量路径——越界就报错，不静默兜底
    with pytest.raises(ServiceUnavailableError, match="向量"):
        R.route_for(R.WorkloadKind.SEMANTIC_RETRIEVAL)


def test_dashboard_workload_goes_to_the_internal_table():
    """大屏要毫秒级且稳定，走 StarRocks 内表——不受湖端 Compaction 影响。"""
    decision = R.route_for(R.WorkloadKind.DASHBOARD_REPORT)
    assert decision.route is R.QueryRoute.STARROCKS_INTERNAL


def test_qualify_prefixes_the_table_per_route():
    from adas_lakehouse.ads.errors import ServiceUnavailableError

    internal = R.qualify(TABLE, R.QueryRoute.STARROCKS_INTERNAL)
    external = R.qualify(TABLE, R.QueryRoute.PAIMON_EXTERNAL)
    assert internal.endswith(f"`{TABLE}`")
    assert external.endswith(f"`{TABLE}`")
    assert internal.count(".") == 1  # `db`.`table`
    assert external.count(".") == 2  # `catalog`.`db`.`table`

    with pytest.raises(ServiceUnavailableError):
        R.qualify(TABLE, R.QueryRoute.VECTOR_INDEX)


# --------------------------------------------------------------------------- 查询服务


def test_fetch_returns_only_the_requested_columns():
    columns = S.column_names(TABLE)[:3]
    result = _service().fetch(Q.AdsQuery(table=TABLE, columns=columns, limit=2))

    assert result.table == TABLE
    assert len(result.rows) == 2
    assert set(result.rows[0]) == set(columns)
    assert result.route in set(R.QueryRoute)


def test_unknown_column_is_rejected_before_hitting_the_source():
    with pytest.raises(Q.UnknownColumnError):
        _service().fetch(Q.AdsQuery(table=TABLE, columns=("no_such_col",)))


def test_limit_and_offset_page_the_result():
    svc = Q.AdsQueryService(Q.StaticRowSource({TABLE: _rows(TABLE, 5)}))
    page1 = svc.fetch(Q.AdsQuery(table=TABLE, limit=2, offset=0))
    page2 = svc.fetch(Q.AdsQuery(table=TABLE, limit=2, offset=2))
    assert len(page1.rows) == len(page2.rows) == 2
    assert page1.rows[0] != page2.rows[0]


def test_cache_is_used_on_the_second_identical_query():
    svc = _service()
    query = Q.AdsQuery(table=TABLE, limit=2)
    first = svc.fetch(query)
    second = svc.fetch(query)
    assert first.from_cache is False
    assert second.from_cache is True

    svc.invalidate_cache()
    assert svc.fetch(query).from_cache is False


def test_filters_are_validated_against_the_operator_whitelist():
    """算子白名单是防 SQL 注入的那道门——不在表里的一律拒绝。"""
    from adas_lakehouse.ads.errors import InvalidFilterError

    assert "IN" in Q.SUPPORTED_OPERATORS
    Q.Filter(column="stat_date", op="=", value="2026-03-01")

    with pytest.raises(InvalidFilterError, match="不支持的算子"):
        Q.Filter(column="stat_date", op="; DROP TABLE x; --", value="1")


# --------------------------------------------------------------------------- 网关


def _principal() -> G.Principal:
    return G.Principal(
        platform=list(P.BusinessPlatform)[0], subject="alice", scopes=frozenset({"*"})
    )


def test_gateway_rejects_an_unknown_token():
    from adas_lakehouse.ads.errors import AuthenticationError

    auth = G.TokenAuthenticator({"good-token": _principal()})
    assert auth.authenticate("good-token").subject == "alice"
    with pytest.raises(AuthenticationError):
        auth.authenticate("bad-token")


def test_rate_limiter_empties_the_bucket_and_counts_per_subject():
    from adas_lakehouse.ads.errors import RateLimitExceededError

    limiter = G.TokenBucketRateLimiter(burst=2)
    limiter.acquire("alice", qps=0)
    limiter.acquire("alice", qps=0)  # 桶里就 2 个令牌，qps=0 不回填
    with pytest.raises(RateLimitExceededError, match="alice"):
        limiter.acquire("alice", qps=0)

    limiter.acquire("bob", qps=0)  # 按主体独立计数，alice 被限不影响 bob

    limiter.reset("alice")
    limiter.acquire("alice", qps=0)


def test_audit_log_records_every_call():
    log = G.AuditLog()
    log.record(
        G.AuditRecord(
            at=datetime(2026, 3, 1, 12, 0, 0),
            subject="alice",
            platform=list(P.BusinessPlatform)[0],
            path="/ads/closed-loop-dashboard",
            params={"stat_date": "2026-03-01"},
            ok=True,
            elapsed_ms=12.3,
        )
    )
    recent = list(log.recent())
    assert len(recent) == 1
    assert recent[0]["subject"] == "alice"
    assert recent[0]["ok"] is True


# --------------------------------------------------------------------------- 物化


def test_every_ads_table_has_a_materialize_plan():
    assert set(M.PLANS) == set(P.ADS_TABLE_NAMES)


def test_flink_sql_renders_for_every_ads_table():
    """11 张 ADS 表各一个批作业，文件名即表名。"""
    rendered = M.render_all_flink_sql()
    assert set(rendered) == {f"{name}.sql" for name in P.ADS_TABLE_NAMES}
    for filename, sql in rendered.items():
        table = filename.removesuffix(".sql")
        assert table in sql
        assert "INSERT INTO" in sql.upper()
