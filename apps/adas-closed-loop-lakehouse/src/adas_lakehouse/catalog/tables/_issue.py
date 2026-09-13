"""分析域（issue_）：问题记录与分析。

闭环链路的「问题出口」——评测 Badcase、路测反馈、影子模式异常统一收敛成问题单，
在此做根因归类、责任归属与解决时效跟踪，支撑 DWS 的 Badcase 解决率与
ADS 的根因分布。表清单见系列二第二章（ODS）与第六章（DWD）。

结构与注释风格对齐采集域参考实现 _collect.py。
"""

from __future__ import annotations

from ...domains import DataDomain, Layer
from ..spec import Column as C
from ..spec import TableSpec

D = DataDomain.ISSUE

TABLES: list[TableSpec] = [
    TableSpec(
        name="ods_issue_record",
        layer=Layer.ODS,
        domain=D,
        comment="问题单记录（原样同步，不做根因归一）",
        source_system="问题管理平台MySQL",
        bucket=4,
        primary_key=("issue_id",),
        notes=(
            "分区规则三：主键 Upsert 且无明确分区维度 → 不分区。"
            "问题单是长生命周期实体（创建后状态反复更新），按 issue_id Upsert；"
            "源系统枚举值不统一（如 severity 混用 P0/高），归一化留给 DWD"
        ),
        columns=[
            C("issue_id", "STRING", "问题单 ID，源系统业务主键", nullable=False),
            C("issue_title", "STRING", "问题标题"),
            C(
                "issue_type",
                "STRING",
                "问题类型：perception/prediction/planning/control/data_quality",
            ),
            C(
                "issue_source",
                "STRING",
                "问题来源：evaluation_badcase/road_test/shadow_mode/customer_feedback",
            ),
            C("project_code", "STRING", "所属项目"),
            C("severity", "STRING", "严重等级（源系统原值，未归一）：P0/P1/P2/P3"),
            C("priority", "STRING", "处理优先级"),
            C(
                "issue_status",
                "STRING",
                "问题状态：open/analyzing/fixing/verifying/closed/rejected",
            ),
            C("root_cause_category", "STRING", "根因分类（源系统人工填写）"),
            C("root_cause_desc", "STRING", "根因描述"),
            C("badcase_id", "STRING", "关联 Badcase ID（来源为评测时非空）"),
            C("data_id", "STRING", "关联 clip 的 data_id，回溯原始采集片段"),
            C("vehicle_code", "STRING", "复现车辆编码"),
            C("model_version", "STRING", "问题暴露时的模型版本"),
            C("owner_user", "STRING", "责任人工号"),
            C("reporter_user", "STRING", "提单人工号"),
            C("create_time", "TIMESTAMP(3)", "问题创建时间"),
            C("resolve_time", "TIMESTAMP(3)", "问题解决时间"),
            C("close_time", "TIMESTAMP(3)", "问题关闭时间"),
        ],
    ),
    TableSpec(
        name="dwd_issue_detail",
        layer=Layer.DWD,
        domain=D,
        comment="问题明细（根因归一 + 挂 data_id 回溯链路）",
        bucket=4,
        primary_key=("issue_id",),
        notes=(
            "主键取业务主键 issue_id 而非 data_id：一个 clip 可暴露多个问题，"
            "data_id / artifact_id 作为关联键冗余落表，使「从问题回溯到原始 clip」"
            "成为一次主键查询。问题单总量远小于 clip 明细，bucket 取中等档 4"
        ),
        columns=[
            C("issue_id", "STRING", "问题单 ID", nullable=False),
            C("data_id", "STRING", "一级 ID：关联 clip 级终身锚点，回溯血缘起点"),
            C("artifact_id", "STRING", "二级 ID：问题定位到的处理产物"),
            C("badcase_id", "STRING", "关联 Badcase ID"),
            C("evaluation_type", "STRING", "暴露该问题的评测类型"),
            C("project_code", "STRING", "所属项目"),
            C("vehicle_code", "STRING", "复现车辆编码"),
            C("model_version", "STRING", "问题暴露时的模型版本"),
            C(
                "issue_type",
                "STRING",
                "问题类型（已归一）：perception/prediction/planning/control/data_quality",
            ),
            C(
                "issue_source",
                "STRING",
                "问题来源（已归一）：evaluation_badcase/road_test/shadow_mode/customer_feedback",
            ),
            C("severity_level", "STRING", "严重等级（已归一）：P0/P1/P2/P3"),
            C(
                "issue_status",
                "STRING",
                "问题状态：open/analyzing/fixing/verifying/closed/rejected",
            ),
            C("root_cause_category", "STRING", "根因大类（已归一，供 ADS 根因分布聚合）"),
            C("fix_solution", "STRING", "修复方案：模型迭代/数据补采/规则调整/标注返工"),
            C("fix_model_version", "STRING", "修复后验证通过的模型版本"),
            C("owner_user", "STRING", "责任人工号"),
            C("create_time", "TIMESTAMP(3)", "问题创建时间"),
            C("close_time", "TIMESTAMP(3)", "问题关闭时间"),
            C("resolve_duration_hours", "DOUBLE", "解决耗时（小时），DWS Badcase 解决率口径输入"),
            C("reopen_count", "INT", "重开次数，衡量修复质量"),
        ],
    ),
]
