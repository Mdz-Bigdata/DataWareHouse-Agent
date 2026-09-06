# -*- coding: utf-8 -*-
"""Metadata-bound period comparisons; failures never produce synthetic results."""
import copy
import logging
import math
import re
import time
from typing import Any, Tuple

import pandas as pd
import sqlglot
from sqlglot import exp

from app.service.skills.base_skill import BaseSkill, SkillContext, SkillResult
from app.service.db_service import db_service
from app.service.guardrail import guardrail, GuardrailException
from app.service.semantic_layer import semantic_layer, DSLCompiler
from app.service.date_ranges import question_periods

logger = logging.getLogger(__name__)


class AnalysisUnavailable(ValueError):
    """A user-actionable analysis limitation, safe to include in the response."""


class AttributionSkill(BaseSkill):
    name: str = "attribution_skill"
    description: str = "指标异动归因诊断与多维度贡献度下钻拆解技能"
    TRIGGER_KEYWORDS = [
        "为什么", "原因", "归因", "异动", "变动", "下降", "上升", "暴跌", "暴涨",
        "贡献度", "下钻", "波动", "诊断", "下滑", "增幅", "降幅",
    ]
    # These are business terms, never physical table mappings.
    METRIC_TERMS = [
        (("退款率",), ("refund_ratio", "refund_rate", "退款率")),
        (("退款", "refund"), ("refund_amount", "退款金额", "退款额")),
        (("完播",), ("completion_rate", "完播率")),
        (("播放", "收听", "play"), ("play_count", "播放量", "收听量")),
        (("订单", "order"), ("order_count", "订单数", "订单量")),
        (("gmv", "销售", "收入", "交易额"), ("gmv", "销售额", "交易额", "收入")),
    ]
    DIMENSION_TERMS = [
        (("区域", "地区", "region"), "region_name"),
        (("套餐", "规格", "plan"), "plan_name"),
        (("主播", "演播", "anchor"), "anchor_name"),
        (("品类", "分类", "类目", "category"), "category_name"),
        (("来源", "发布平台", "source_platform"), "source_platform"),
    ]

    def can_handle(self, ctx: SkillContext) -> Tuple[bool, float]:
        matched = any(kw in (ctx.rewritten_question or ctx.question).lower()
                      for kw in self.TRIGGER_KEYWORDS)
        return (True, 0.95) if matched else (False, 0.0)

    @staticmethod
    def _mentions(question, term):
        term = term.lower().strip()
        if not term:
            return False
        if re.fullmatch(r"[a-z0-9_]+", term):
            return bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", question))
        return term in question

    def _metric(self, question):
        metric_question = question
        # Contribution percentages describe the requested analysis output, not
        # the input metric (for example refund amount + contribution rate).
        for output_term in ("贡献率", "贡献比例", "贡献占比", "占比", "contribution_rate", "contribution_ratio"):
            metric_question = metric_question.replace(output_term, "")
        rate_intent = any(term in metric_question for term in ("率", "比例", "比率", "除以", "_rate", "_ratio"))
        candidates = []
        tables = semantic_layer.mentioned_tables(question)
        eligible = [metric for metric in semantic_layer.metrics.values()
                    if not tables or metric.source_table in tables]
        for metric in eligible:
            matches = [len(term) for term in [metric.name, *metric.aliases]
                       if self._mentions(question, term)]
            if matches:
                candidates.append((max(matches), metric))
        if not candidates:
            terms = next((terms for cues, terms in self.METRIC_TERMS
                          if any(cue in question for cue in cues)), ())
            for metric in eligible:
                aliases = {term.lower() for term in [metric.name, *metric.aliases]}
                if any(term in aliases for term in terms):
                    candidates.append((1, metric))
        if rate_intent:
            candidates = [(score, metric) for score, metric in candidates if self._is_rate(metric)]
            if not candidates:
                raise ValueError("比率归因需要注册对应比率指标并配置分子、分母口径；当前不能用金额或数量替代该比率。")
        is_audio = any(cue in question for cue in ("听书", "会员", "播放", "收听", "完播", "主播", "专辑"))
        def belongs_to_audio(metric):
            return any(cue in " ".join([metric.name, metric.source_table, *metric.aliases]).lower()
                       for cue in ("audio", "listen", "听书", "会员", "播放", "收听", "完播"))

        if is_audio:
            candidates = [(score, metric) for score, metric in candidates if belongs_to_audio(metric)]
        else:
            trade_candidates = [(score, metric) for score, metric in candidates if not belongs_to_audio(metric)]
            if trade_candidates:
                candidates = trade_candidates
        if not candidates:
            raise ValueError("当前数据源未注册与问题匹配的指标。请先连接业务数据源并同步表结构、注册指标，或在问题中指定已注册的指标名称。")
        best_score = max(score for score, _ in candidates)
        best = [metric for score, metric in candidates if score == best_score]
        if len(best) != 1:
            raise ValueError("匹配到多个业务指标，请在问题中明确指标名称：" + "、".join(m.name for m in best))
        return best[0]

    @staticmethod
    def _is_rate(metric):
        return (metric.unit == "%" or metric.name.lower().endswith(("_rate", "_ratio"))
                or metric.calculation.lower().endswith(("_rate", "_ratio"))
                or any(alias.endswith("率") for alias in metric.aliases))

    @staticmethod
    def _dimension_candidates(layer, metric, name=None):
        dimensions = { (d.source_table, d.name): d for d in
                       [*layer.dimensions.values(), *layer.table_dimensions.values()] }
        measures = {(m.source_table, m.calculation) for m in layer.metrics.values()
                    if m.default_agg.upper() in ("SUM", "AVG")}
        candidates = [d for d in dimensions.values()
                      if d.name in metric.available_dimensions and (name is None or d.name == name)
                      and (d.source_table, d.source_column) not in measures
                      and (d.source_table == metric.source_table
                           or layer.get_join_path_chain(metric.source_table, d.source_table))]
        if name:
            local = [d for d in candidates if d.source_table == metric.source_table]
            return local or candidates
        return candidates

    def _dimension(self, layer, metric, question):
        region_filtered = any(region in question for region in ("华东", "华北", "华南", "华中"))
        region_grouping = bool(re.search(r"(?:按|各|每个)(?:区域|地区|大区)|按region", question))
        scored = [(max((len(term) for term in [d.name, *d.aliases]
                        if self._mentions(question, term)), default=0), d)
                  for d in self._dimension_candidates(layer, metric)
                  if not (region_filtered and not region_grouping and d.name == "region_name")]
        best_score = max((score for score, _ in scored), default=0)
        if best_score:
            candidates = [d for score, d in scored if score == best_score]
            local = [d for d in candidates if d.source_table == metric.source_table]
            candidates = local or candidates
        else:
            requested = next((name for cues, name in self.DIMENSION_TERMS
                              if any(cue in question for cue in cues)), None)
            if requested == "region_name" and region_filtered and not region_grouping:
                requested = None
            default = "plan_name" if "会员" in question and "退款" in question else "category_name"
            candidates = self._dimension_candidates(layer, metric, requested or default)
        if not candidates:
            raise ValueError("该指标缺少所需的下钻维度或表关联关系。请同步维度表与 JOIN 关系，或明确指定指标支持的维度。")
        if len(candidates) != 1:
            raise ValueError("下钻维度对应多个来源表，请在语义层明确维度与指标的关联后重试。")
        return candidates[0]

    @staticmethod
    def _bind_dimension(layer, metric, dimension):
        # A request-local binding keeps compiler resolution consistent without changing the registry.
        layer.dimensions[dimension.name] = dimension
        layer.table_dimensions[(metric.source_table, dimension.name)] = dimension

    @staticmethod
    def _validate_schema(layer, sql, dialect):
        expression = sqlglot.parse_one(sql, read=dialect)
        tables = list(dict.fromkeys(table.name for table in expression.find_all(exp.Table)))
        columns = {table: {column[0] for column in layer.discovered_table_columns.get(table, [])}
                   for table in tables}
        if not tables or any(not columns[table] for table in tables):
            raise ValueError("指标或维度依赖的业务表尚未在当前数据源中发现。请检查数据源连接并重新同步表结构。")
        for column in expression.find_all(exp.Column):
            if column.table and (column.table not in columns or column.name not in columns[column.table]):
                raise ValueError("指标或维度引用的字段与当前表结构不一致。请重新同步表结构并更新指标与关联配置。")
        return tables

    def _period_comparison(self, ctx, layer, metric, dimension, dsl, periods, details, started):
        """Compute additive period changes from two guarded queries, never canned answers."""
        current_period, baseline_period = periods
        sqls, frames = [], []
        for period in (current_period, baseline_period):
            period_dsl = copy.deepcopy(dsl)
            period_dsl["time_range"] = period
            guardrail.check_dsl(period_dsl, layer, user_role=ctx.role)
            sql = DSLCompiler(layer=layer, dialect=ctx.dialect).compile(period_dsl)
            self._validate_schema(layer, sql, ctx.dialect)
            guardrail.check_sql(sql, dialect=ctx.dialect)
            frame = db_service.execute_query(sql, dialect=ctx.dialect)
            alias = DSLCompiler.aggregate_alias(metric.name)
            values = pd.to_numeric(frame[alias], errors="raise")
            valid = values.notna()
            if not all(math.isfinite(float(value)) for value in values[valid]):
                raise AnalysisUnavailable("查询结果包含非有限数值，无法计算贡献度。")
            # Preserve NULL as a separate group from a literal category named “其他”.
            groups = {None if pd.isna(row[dimension.name]) else str(row[dimension.name]): float(value)
                      for (_, row), value in zip(frame.loc[valid].iterrows(), values[valid])}
            sqls.append(sql)
            frames.append(groups)
        current, baseline = frames
        if not current or not baseline:
            raise AnalysisUnavailable("分析期或对比期没有有效数据，无法计算变化贡献。请调整日期范围。")
        current_value, baseline_value = sum(current.values()), sum(baseline.values())
        total_change = current_value - baseline_value
        zero_change = math.isclose(total_change, 0.0, abs_tol=1e-9)
        items, records = [], []
        for key in current.keys() | baseline.keys():
            name = "未分类（空值）" if key is None else key
            before, after = baseline.get(key, 0.0), current.get(key, 0.0)
            change = after - before
            ratio = 0.0 if zero_change else change / total_change * 100
            items.append({"name": name, "value": change, "ratio": ratio,
                          "baseline_value": before, "current_value": after})
        items.sort(key=lambda item: (-abs(item["value"]), item["name"]))
        for item in items:
            records.append({"dimension_slice": item["name"], "baseline_value": item["baseline_value"],
                            "current_value": item["current_value"], "change": item["value"],
                            "contribution_rate": "—" if zero_change else f"{item['ratio']:.2f}%"})
        metric_display = next((a for a in metric.aliases if re.search(r"[\u4e00-\u9fff]", a)), metric.name)
        dim_display = next((a for a in dimension.aliases if re.search(r"[\u4e00-\u9fff]", a)), dimension.name)
        demo = db_service.is_sample_data
        rate = total_change / baseline_value * 100 if baseline_value else None
        def period_label(period):
            return f"{period['start']} 至 {period['end']}"
        direction = "增加" if total_change > 0 else "减少" if total_change < 0 else "持平"
        prefix = ("【演示数据】" if db_service.real_engine is None else "【项目示例数据】") if demo else ""
        conclusion = (f"{prefix}「{metric_display}」在 {period_label(current_period)} "
                      f"合计 {current_value:,.2f} {metric.unit}，对比 {period_label(baseline_period)} "
                      f"的 {baseline_value:,.2f} {metric.unit}，{direction} {abs(total_change):,.2f} {metric.unit}。")
        if items and not zero_change:
            conclusion += (f"按{dim_display}拆解，绝对变化最大的分组是「{items[0]['name']}」，"
                           f"变化 {items[0]['value']:+,.2f} {metric.unit}。")
        conclusion += "贡献度按各分组变化额÷总变化额计算，用于定位数值变化来源，不代表因果证明。"
        details.update({"sql": "-- 分析期\n" + sqls[0] + ";\n\n-- 对比期\n" + sqls[1] + ";",
                        "elapsed_time": f"{time.perf_counter() - started:.3f}s",
                        "source_desc": f"{'项目示例数据；' if demo else ''}两期实际查询结果的变化贡献分解",
                        "time_scope": f"分析期 {period_label(current_period)}；对比期 {period_label(baseline_period)}"})
        return SkillResult(
            success=True, skill_type="attribution", conclusion=conclusion, data=records, details=details,
            column_types={"dimension_slice": "string", "baseline_value": "decimal", "current_value": "decimal",
                          "change": "decimal", "contribution_rate": "string"},
            chart={"type": "bar", "title": f"{metric_display}变化贡献", "config": {
                "xAxis": {"data": [item["name"] for item in items]}, "series": [
                    {"name": "变化额", "type": "bar", "data": [item["value"] for item in items]}]}},
            attribution_data={"analysis_type": "period_comparison", "metric_name": metric.name,
                              "metric_display": metric_display, "metric_unit": metric.unit,
                              "dimension": dimension.name, "dimension_display": dim_display,
                              "current_period": current_period, "baseline_period": baseline_period,
                              "current_value": current_value, "baseline_value": baseline_value,
                              "total_value": current_value, "total_change": total_change, "change_rate": rate,
                              "top_driver": items[0]["name"] if items else "",
                              "top_driver_ratio": items[0]["ratio"] if items else 0.0,
                              "waterfall_items": items})

    def execute(self, ctx: SkillContext) -> SkillResult:
        started = time.perf_counter()
        question = (ctx.rewritten_question or ctx.question).lower()
        details: dict[str, Any] = {"sql": "", "dialect": ctx.dialect, "tables": [], "filters": [],
                   "source_desc": "指标分组占比分析未完成"}
        demo = db_service.is_sample_data
        details["data_source"] = "demo" if db_service.real_engine is None else "configured"

        def failure(message, clarify=True):
            details["elapsed_time"] = f"{time.perf_counter() - started:.3f}s"
            return SkillResult(success=False, skill_type="attribution", error=message,
                               data=[], details=details,
                               clarification=({"need_clarification": True, "message": message, "options": []}
                                              if clarify else None))

        if not semantic_layer.discovered_table_columns:
            return failure("当前数据源尚未发现业务表，无法进行下钻分析。请检查数据源连接、表访问权限，并同步表结构和指标元数据。")
        try:
            metric = self._metric(question)
            layer = copy.copy(semantic_layer)
            layer.dimensions = dict(semantic_layer.dimensions)
            layer.table_dimensions = dict(semantic_layer.table_dimensions)
            dimension = self._dimension(layer, metric, question)
            self._bind_dimension(layer, metric, dimension)
            is_rate = self._is_rate(metric)
            if is_rate or metric.default_agg.upper() not in ("SUM", "COUNT"):
                return failure("当前下钻支持可累加指标的分组占比。请选择金额或数量指标；比率指标需配置分子、分母后再进行归因。")
            dsl: dict[str, Any] = {"metrics": [{"name": metric.name}], "dimensions": [{"name": dimension.name}],
                   "filters": [], "limit": None}
            # Explicit business filters apply identically to both comparison periods.
            for region in ("华东", "华北", "华南", "华中"):
                if region in question:
                    dsl["filters"].append({"field": "region_name", "op": "eq", "value": region})
                    break
            periods = None
            if DSLCompiler(layer=layer, dialect=ctx.dialect)._resolve_time_column(metric.source_table):
                periods = question_periods(question)
                dsl["time_range"] = periods[0]
            guardrail.check_dsl(dsl, layer, user_role=ctx.role)
            for filt in dsl["filters"]:
                candidates = self._dimension_candidates(layer, metric, filt["field"])
                if len(candidates) != 1:
                    raise ValueError("权限过滤维度缺少明确的表关联，请完善该指标的区域维度配置后重试。")
                self._bind_dimension(layer, metric, candidates[0])
            details["filters"] = dsl["filters"]
            sql = DSLCompiler(layer=layer, dialect=ctx.dialect).compile(dsl)
            details["tables"] = self._validate_schema(layer, sql, ctx.dialect)
        except GuardrailException as error:
            return failure(str(error), clarify=str(error).startswith("语义审计拦截:"))
        except ValueError as error:
            return failure(str(error))
        except Exception:
            logger.exception("[AttributionSkill] 无法解析语义层配置")
            return failure("无法解析指标与维度配置。请检查数据源表结构及语义层配置后重试。")

        try:
            if periods:
                return self._period_comparison(ctx, layer, metric, dimension, dsl, periods, details, started)
            guardrail.check_sql(sql, dialect=ctx.dialect)
            frame = db_service.execute_query(sql, dialect=ctx.dialect)
            values = pd.to_numeric(frame[DSLCompiler.aggregate_alias(metric.name)], errors="raise")
            # NULL aggregates represent no measured value; they must not become invented numbers.
            valid = values.notna()
            frame, values = frame.loc[valid], values.loc[valid]
            if not all(math.isfinite(float(value)) for value in values):
                raise ValueError("non-finite aggregate")
        except AnalysisUnavailable as error:
            return failure(str(error))
        except Exception as error:
            logger.error("[AttributionSkill] 真实数据下钻查询失败 (%s)", type(error).__name__)
            return failure("下钻查询失败，未生成分析结果。请检查当前数据源连接、业务表及字段是否存在、查询权限，并同步最新表结构后重试。", clarify=False)

        total = float(values.sum())
        records, items = [], []
        for (_, row), value in zip(frame.iterrows(), values):
            name = "其他" if pd.isna(row[dimension.name]) else str(row[dimension.name])
            value = float(value)
            ratio = round(value / total * 100, 2) if total else 0.0
            records.append({"dimension_slice": name, "value": round(value, 2),
                            "contribution_rate": f"{ratio}%" if total else "—"})
            items.append({"name": name, "value": round(value, 2), "ratio": ratio})
        metric_display = next((alias for alias in metric.aliases if re.search(r"[\u4e00-\u9fff]", alias)), metric.name)
        dim_display = next((alias for alias in dimension.aliases if re.search(r"[\u4e00-\u9fff]", alias)), dimension.name)
        prefix = ("【演示数据】" if db_service.real_engine is None else "【项目示例数据】") if demo else ""
        if records:
            conclusion = (f"{prefix}「{metric_display}」按「{dim_display}」分组，合计 {total:,.2f} {metric.unit}。"
                          f"当前查询中数值最高的分组为「{items[0]['name']}」。"
                          "此结果展示分组数值及占比，尚未比较基期与现期，不能据此确定涨跌原因。")
        else:
            conclusion = f"{prefix}当前查询范围内暂无「{metric_display}」的有效下钻数据。"
        details.update({"sql": sql, "elapsed_time": f"{time.perf_counter() - started:.3f}s",
                        "source_desc": f"{'项目示例数据；' if demo else ''}指标分组占比分析",
                        "time_scope": "按语义编译器时间规则：业务表存在日期列时默认近30天，否则为全表"})
        return SkillResult(
            success=True, skill_type="attribution", conclusion=conclusion, data=records,
            column_types={"dimension_slice": "string", "value": "decimal", "contribution_rate": "string"},
            chart={"type": "bar", "title": f"{metric_display}按{dim_display}分组",
                   "config": {"xAxis": {"data": [item["name"] for item in items]},
                              "series": [{"name": metric_display, "type": "bar",
                                          "data": [item["value"] for item in items]}]}},
            attribution_data={"metric_name": metric.name, "metric_display": metric_display,
                              "metric_unit": metric.unit,
                              "dimension": dimension.name, "dimension_display": dim_display,
                              "total_value": total, "top_driver": items[0]["name"] if items else "",
                              "top_driver_ratio": items[0]["ratio"] if items else 0.0,
                              "waterfall_items": items, "analysis_type": "dimension_breakdown"},
            details=details,
        )


attribution_skill = AttributionSkill()
