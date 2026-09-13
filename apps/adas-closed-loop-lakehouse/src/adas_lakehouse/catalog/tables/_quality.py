"""质量门禁（伪域 quality_）：入湖闸门的异常隔离表。

独立于 11 数据域之外——它不是某个业务环节的产物，而是三通道（CDC / Kafka / OSS）
写入 ODS 之前那道统一闸门的产物。命中「拒绝入湖」硬规则的数据不丢弃，而是连同
命中规则一起落到这张表里等待处置，这是整套门禁「可重放、可审计」的物理基础。

五步异常闭环（系列二第 6 篇《数据质量门禁设计》第五章）：
  ① 门禁拦截 → ② 异常隔离（本表）→ ③ 分级告警（P0 电话+钉钉 / P1 钉钉+工单 /
  P2 日报 / P3 周报）→ ④ 分流处置（A 自动修复 / B 人工修复 / C 弃置归档）→
  ⑤ 复验重入湖（超 3 轮升级 P0）

字段按「可重放、可追责」两条线设计：
  可重放 = 原始报文 + 原始定位键 + 目标表 + 通道；
  可追责 = 命中规则 + 六维维度 + 严重度 + 异常等级 + 处置人 + 处置状态 + 复验轮次。

⚠️ 列名并存说明：quality 子系统（quality/tables.py 的表规格、quality/isolation.py 的
IssueRecord 与三个 IssueStore 实现）读写的是 source_table / record_key / rule_ids /
detected_at / issue_status 这一组列名，注册表早期定义的是 target_table /
source_record_key / rule_id / isolate_time / handle_status 这一组。按「注册表只增不减」
的约束，两组同义列现在并存，子系统那组已并入本表（见 columns 里的分隔注释与对照表），
SLA / 响应 / 闭环追踪列同批并入。收敛为一组列名需要同步改 quality 子系统与
flink/sql/ 的隔离分支，属于另一次施工。
"""

from __future__ import annotations

from ...domains import QUALITY_GATE_PSEUDO_DOMAIN, DataDomain, Layer
from ..spec import Column as C
from ..spec import TableSpec

#: 伪域没有 DataDomain 成员可挂。门禁是生产链路入口的一部分，故暂挂生产域；
#: 真正的归属以表的 notes 与 domains.QUALITY_GATE_PSEUDO_DOMAIN 为准。
D = DataDomain.PRODUCTION

TABLES: list[TableSpec] = [
    TableSpec(
        name="ods_quality_issue",
        layer=Layer.ODS,
        pseudo_domain="quality_",  # 非 11 数据域，入湖闸门的产物
        # 表名第二段是伪域 quality_，不在 11 数据域内，登记为已知偏离
        name_omits_domain=True,
        domain=D,
        comment="入湖质量门禁异常隔离表（拦截数据原样留存，支撑复验重入湖）",
        source_system="入湖质量门禁服务",
        bucket=4,
        partition_by=("dt",),
        primary_key=("issue_id", "dt"),
        notes=(
            f"质量门禁伪域（{QUALITY_GATE_PSEUDO_DOMAIN}），不属于 11 数据域，"
            "见 domains.QUALITY_GATE_PSEUDO_DOMAIN；domain 暂挂生产域仅为满足注册表类型要求。"
            "分区规则一：异常量随上游批量变更突增，按 dt 分区支撑按天 TTL 清理；"
            "主键原则三：分区表主键必须包含分区字段，故 PK=(issue_id, dt)"
        ),
        columns=[
            C("issue_id", "STRING", "异常记录 ID，门禁拦截瞬间生成的业务主键", nullable=False),
            C("dt", "STRING", "隔离日期分区（yyyy-MM-dd），按天 TTL 清理", nullable=False),
            C("data_id", "STRING", "一级 ID：关联的 clip 锚点；ID 格式非法被拦时可能为空"),
            C("project_code", "STRING", "所属项目"),
            C("vehicle_code", "STRING", "来源车辆编码"),
            C("source_channel", "STRING", "入湖通道：cdc/kafka/oss"),
            C("target_table", "STRING", "本应写入的目标 ODS 表名，复验通过后的重放目的地"),
            C(
                "source_record_key",
                "STRING",
                "原始记录定位键：CDC 主键 / Kafka event_id / OSS object_key",
            ),
            C("rule_id", "STRING", "命中的门禁规则 ID（规则中心 YAML 配置注册）"),
            C(
                "rule_dimension",
                "STRING",
                "六维检查维度：completeness/accuracy/consistency/uniqueness/validity/timeliness",
            ),
            C("severity", "STRING", "检查器严重度：ERROR→拒绝入湖 / WARNING→带标记放行"),
            C(
                "issue_level",
                "STRING",
                "异常等级：P0 合规（脱敏缺失/主键为空/整帧缺失，30 分钟响应）"
                "/P1 严重/P2 一般/P3 观察",
            ),
            C("issue_detail", "STRING", "规则命中详情：期望值 vs 实际值"),
            C("raw_payload", "STRING", "被拦截的原始报文（JSON），原始数据不丢失是可重放的根基"),
            C("isolate_time", "TIMESTAMP(3)", "隔离时间：写入本表的时刻，告警 SLA 计时起点"),
            C("handle_strategy", "STRING", "分流处置：A 自动修复 / B 人工修复 / C 弃置归档"),
            C(
                "handle_status",
                "STRING",
                "处置状态：pending/handling/rechecking/resolved/archived",
            ),
            C("handle_owner", "STRING", "处置责任人：数据 owner 或平台值班"),
            C("recheck_round", "INT", "复验轮次，复验不通过退回隔离，超 3 轮升级 P0"),
            C("resolved_time", "TIMESTAMP(3)", "闭环时间：复验通过重入湖或归档的时刻"),
            # ======================================================================
            # 以下为 quality 子系统（quality/tables.py 的表规格、quality/isolation.py 的
            # IssueRecord）实际读写的列，按「只增不减」并入注册表。
            #
            # 与上方旧列的同义对照（两套列名并存，旧列一列未删）：
            #   source_table≈target_table   record_key≈source_record_key
            #   rule_ids≈rule_id            dimension≈rule_dimension
            #   detail≈issue_detail         detected_at≈isolate_time
            #   repair_action≈handle_strategy  issue_status≈handle_status
            #   owner≈handle_owner          recheck_count≈recheck_round
            #   resolved_at≈resolved_time
            # 并入的列一律可空：同语义的旧列还在，且 flink/sql/ 的隔离分支仍按旧列名
            # INSERT，并存期把新列设成 NOT NULL 会让既有写入路径直接写不进来。
            # ======================================================================
            # ---- ① 门禁拦截 · 找得回：原始数据定位 ----
            C(
                "detected_at",
                "TIMESTAMP(3)",
                "门禁拦截时刻（原文第五章 ① 门禁拦截），告警 SLA 计时起点",
            ),
            C("source_table", "STRING", "被拦截数据本应写入的目标 ODS 表——复验通过后的重放目的地"),
            C(
                "source_system",
                "STRING",
                "来源系统标识（业务列，区别于 ODS 系统字段 _source_system）",
            ),
            C(
                "record_key",
                "STRING",
                "被拦截记录的业务定位键：CDC 主键 / Kafka event_id / OSS object_key",
            ),
            C("artifact_id", "STRING", "二级 ID：处理产物 ID"),
            C("run_id", "STRING", "三级 ID：处理运行 ID"),
            C("parent_artifact_id", "STRING", "血缘父产物 ID，冗余落表以便图库对账兜底"),
            # ---- 说得清：命中规则 + 规则等级 ----
            C(
                "rule_ids",
                "STRING",
                "命中的规则 ID 列表（逗号分隔）。原文第三章：被拒绝的数据连同命中规则一起落表，"
                "而不是打日志了事——一次检查可命中多条规则，故是列表不是单值",
            ),
            C(
                "dimension",
                "STRING",
                "六维检查维度：completeness/accuracy/consistency/uniqueness/validity/timeliness",
            ),
            C(
                "quality_layer",
                "STRING",
                "五层质量问题定位（原文第一章全景表）：L1 传感器 / L2 量产车回传 / L3 标注 / "
                "L4 分布与场景 / L5 数据工程与训练评测",
            ),
            C("message", "STRING", "命中说明（规则声明里的 message）"),
            C("detail", "STRING", "检查器给出的具体偏差：期望值 vs 实际值"),
            C("hits_json", "STRING", "全部命中明细（JSON 数组），逐条带 rule_id/severity/detail"),
            # ---- ② 异常隔离 · 可重放上下文 ----
            C("payload_hash", "STRING", "原始报文哈希，参与 issue_id 派生，保证重放/重试幂等"),
            C(
                "payload_object_key",
                "STRING",
                "超大报文的对象存储 key：内联超过 thresholds.RAW_PAYLOAD_INLINE_MAX_BYTES 时退化为外置",
            ),
            C("replayable", "BOOLEAN", "是否具备重放条件（原始报文完整 + 目标表已知）"),
            # ---- ③④⑤ 分级告警 / 分流处置 / 复验重入湖 · 修得好 ----
            C(
                "issue_status",
                "STRING",
                "五步闭环状态机：isolated/alerted/dispatched/repaired/rechecking/reingested/discarded",
            ),
            C(
                "repair_action",
                "STRING",
                "④ 分流处置：A 自动修复（重传/幂等重放/断点续传）/ B 人工修复（源端补数，工单跟踪）"
                "/ C 弃置归档（无法修复，标记原因后归档保留审计）",
            ),
            C(
                "recheck_count",
                "INT",
                "⑤ 复验轮次：复验不通过退回隔离，超 3 轮升级 P0（thresholds.MAX_RECHECK_ROUNDS）",
            ),
            C("escalated", "BOOLEAN", "是否已升级（超 3 轮复验升 P0；P3 连续两周超标升 P2）"),
            C("owner", "STRING", "数据 owner——③ 分级告警的通知对象之一"),
            C("on_duty", "STRING", "平台值班——③ 分级告警的通知对象之一"),
            # ---- SLA：四档响应要求（原文第五章「四个异常等级对应四档响应 SLA」）----
            C(
                "response_due_at",
                "TIMESTAMP(3)",
                "响应截止时间：P0 电话+钉钉 30 分钟内 / P1 钉钉+工单 2 小时内",
            ),
            C(
                "closure_due_at",
                "TIMESTAMP(3)",
                "闭环截止时间：P1 当日修复 / P2 日报汇总 3 个工作日内闭环 / P3 周报汇总",
            ),
            C("responded_at", "TIMESTAMP(3)", "实际响应时间"),
            C("resolved_at", "TIMESTAMP(3)", "实际闭环时间"),
            C("sla_met", "BOOLEAN", "是否达成 SLA——闭环度量「异常处理 SLA 达成率」的分子"),
            C(
                "reingested_at",
                "TIMESTAMP(3)",
                "⑤ 复验通过重入湖的时刻——闭环度量「修复重入湖成功率」的依据",
            ),
            C("discard_reason", "STRING", "C 弃置归档原因：无法修复，标记原因后归档保留审计"),
            C(
                "gate_version",
                "STRING",
                "拦下这条数据的门禁版本——规则按表灰度发布后，要能回答「这条异常是哪版门禁拦的」",
            ),
            C("history", "STRING", "处理轨迹（JSON 数组）：五步闭环每次状态推进追加一条，可追责"),
        ],
    ),
]
