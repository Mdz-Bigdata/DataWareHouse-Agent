"""Fast, deterministic SQL templates for canonical audio-domain aggregate plans."""

from __future__ import annotations

import re

from app.services.metric_baseline_service import build_metric_reference_sql, load_audio_metrics


def build_deterministic_sql(
    analysis_plan: dict,
    table_infos: list[dict],
) -> str | None:
    """Build the common audience + genre playback comparison without an LLM.

    The template is intentionally narrow: it only activates when the deterministic
    plan contains every metric and filter needed by the query. All output is still
    passed through the normal SQL guard and EXPLAIN validation.
    """

    metric_requirements = analysis_plan.get("metric_requirements", [])
    count_requirement = _find_metric(metric_requirements, "play_session.id", "COUNT")
    average_requirement = _find_metric(metric_requirements, "play_session.played_seconds", "AVG")
    if not count_requirement or not average_requirement:
        return None
    if average_requirement.get("operation") != "subtract":
        return None

    requirements = analysis_plan.get("filter_requirements", [])
    region = _find_filter(requirements, "user_profile.province")
    gender = _find_filter(requirements, "user_profile.gender")
    member_level = _find_filter(requirements, "member_account.member_level")
    genres = [
        requirement
        for requirement in requirements
        if "dim_audio_category.category_name" in requirement.get("columns", [])
    ]
    if not region or not gender or not member_level or len(genres) != 2:
        return None

    required_tables = {
        "play_session",
        "user_account",
        "user_profile",
        "member_account",
        "audio_album",
        "dim_audio_category",
    }
    available_tables = {str(table.get("name")) for table in table_infos}
    if not required_tables.issubset(available_tables):
        return None

    region_value = _first_value(region)
    gender_value = _first_value(gender)
    member_value = _first_value(member_level)
    first_genre = _first_value(genres[0])
    second_genre = _first_value(genres[1])
    if not all((region_value, gender_value, member_value, first_genre, second_genre)):
        return None

    return (
        "SELECT COUNT(ps.id) AS `播放总次数`, "
        f"AVG(CASE WHEN dac.category_name LIKE '%{_escape(first_genre)}%' "
        "THEN ps.played_seconds END) - "
        f"AVG(CASE WHEN dac.category_name LIKE '%{_escape(second_genre)}%' "
        "THEN ps.played_seconds END) AS `平均播放时长差（秒）` "
        "FROM play_session AS ps "
        "JOIN user_account AS ua ON ps.user_id = ua.id "
        "JOIN user_profile AS up ON up.user_id = ua.id "
        "JOIN member_account AS ma ON ma.user_id = ua.id "
        "JOIN audio_album AS aa ON ps.album_id = aa.id "
        "JOIN dim_audio_category AS dac ON aa.category_id = dac.id "
        f"WHERE (up.province LIKE '%{_escape(region_value)}%' "
        f"OR up.city LIKE '%{_escape(region_value)}%') "
        f"AND up.gender = '{_escape(gender_value)}' "
        f"AND ma.member_level = '{_escape(member_value)}' "
        "AND ma.member_status = 'active' "
        "AND ma.valid_from <= CURRENT_TIMESTAMP "
        "AND ma.valid_to >= CURRENT_TIMESTAMP"
    )


def find_catalog_metric(query: str, metric_infos: list[dict]) -> dict | None:
    """Resolve an unambiguous canonical question to a configured metric."""

    normalized = query.lower().replace("？", "").replace("?", "").replace("，", "")
    unsupported_words = ("top", "排名", "排行", "趋势", "每天", "按天", "比较", "对比", "差")
    if any(word in normalized for word in unsupported_words) or re.search(r"前\s*\d+", normalized):
        return None

    metric = find_metric_by_alias(query, metric_infos, include_catalog=True)
    if metric is None:
        return _find_count_metric_by_phrase(query, metric_infos)
    currency_column = metric.get("currency_column")
    if currency_column and "币种" not in normalized:
        return None

    alias = next(
        (str(item) for item in metric.get("alias", []) if str(item).lower() in normalized),
        "",
    )
    allowed = (
        "平台",
        "当前",
        "累计",
        "总",
        "请",
        "统计",
        "按币种",
        "币种",
        "是多少",
        "多少",
        "个",
        "的",
    )
    residue = normalized.replace(alias.lower(), "")
    for word in allowed:
        residue = residue.replace(word, "")
    return metric if not re.sub(r"\s+", "", residue) else None


def _find_count_metric_by_phrase(query: str, metric_infos: list[dict]) -> dict | None:
    """Resolve natural Chinese count questions such as ``多少个有声专辑``.

    Relaxed matching is limited to COUNT formulas and requires the normalized
    question to equal a configured alias stem. Complex shapes are rejected before
    this helper is reached.
    """

    metrics_by_id = {str(metric["id"]): metric for metric in [*metric_infos, *load_audio_metrics()]}
    question = _normalize_count_phrase(query)
    matches: list[tuple[int, dict]] = []
    for metric in metrics_by_id.values():
        if "COUNT(" not in str(metric.get("formula", "")).upper():
            continue
        for alias_value in metric.get("alias", []):
            alias = _normalize_count_phrase(str(alias_value))
            stems = {alias, _strip_count_suffix(alias)}
            matched = [stem for stem in stems if stem and stem == question]
            if matched:
                matches.append((max(len(stem) for stem in matched), metric))
                break
    if not matches:
        return None
    max_length = max(length for length, _ in matches)
    candidates = {str(metric["id"]): metric for length, metric in matches if length == max_length}
    return next(iter(candidates.values())) if len(candidates) == 1 else None


def _normalize_count_phrase(value: str) -> str:
    normalized = value.lower()
    for source, target in (
        ("有声专辑", "专辑"),
        ("有声书", "专辑"),
        ("音频章节", "章节"),
        ("声音", "章节"),
        ("用户", ""),
        ("已发布的", "已发布"),
        ("启用的", "启用"),
    ):
        normalized = normalized.replace(source, target)
    for word in (
        "平台",
        "当前",
        "累计",
        "一共",
        "总",
        "启用",
        "有多少个",
        "有多少位",
        "多少个",
        "多少位",
        "是多少",
        "多少",
        "的",
        "请",
        "统计",
        "？",
        "?",
        "，",
        ",",
    ):
        normalized = normalized.replace(word, "")
    return re.sub(r"\s+", "", normalized)


def _strip_count_suffix(value: str) -> str:
    for suffix in ("数量", "次数", "人数", "数"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def find_metric_by_alias(
    query: str,
    metric_infos: list[dict],
    *,
    include_catalog: bool = False,
) -> dict | None:
    """Return the unique metric whose longest configured alias occurs in ``query``.

    Complex DSL shapes still need planning, but their metric choice should not be left
    to fuzzy model judgment when the user names a catalog alias verbatim.  Callers in
    the retrieval stage may opt into the published catalog; execution-stage callers
    deliberately use only the metrics recalled for the current request.
    """

    normalized = query.lower().replace("？", "").replace("?", "").replace("，", "")
    candidates_source = [*metric_infos, *(load_audio_metrics() if include_catalog else [])]
    metrics_by_id = {str(metric["id"]): metric for metric in candidates_source}
    matches: list[tuple[int, dict]] = []
    for metric in metrics_by_id.values():
        for alias_value in metric.get("alias", []):
            alias = str(alias_value).strip().lower()
            if alias and alias in normalized:
                matches.append((len(alias), metric))
                break
    if not matches:
        return None
    max_length = max(length for length, _ in matches)
    candidates = {str(metric["id"]): metric for length, metric in matches if length == max_length}
    return next(iter(candidates.values())) if len(candidates) == 1 else None


def build_catalog_metric_sql(query: str, metric_infos: list[dict]) -> str | None:
    """Use the configured metric formula for an unambiguous canonical question.

    This avoids asking the LLM to reconstruct fixed filters (such as effective
    price rules) or aggregation semantics (such as AVG ranking score). The
    narrow shape check prevents this shortcut from absorbing ranking, trend,
    comparison, or additional user filters that need full planning.
    """

    metric = find_catalog_metric(query, metric_infos)
    if metric is None:
        return None
    return build_metric_reference_sql(metric)


def _find_metric(requirements: list[dict], column: str, aggregate: str) -> dict | None:
    return next(
        (
            requirement
            for requirement in requirements
            if requirement.get("column") == column
            and str(requirement.get("aggregate", "")).upper() == aggregate
        ),
        None,
    )


def _find_filter(requirements: list[dict], column: str) -> dict | None:
    return next(
        (requirement for requirement in requirements if column in requirement.get("columns", [])),
        None,
    )


def _first_value(requirement: dict) -> str:
    values = requirement.get("values", [])
    return str(values[0]) if values else ""


def _escape(value: str) -> str:
    return value.replace("'", "''")
