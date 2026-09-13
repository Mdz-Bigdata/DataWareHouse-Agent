# 原文清单（16 篇）

本项目是对下列 16 篇公众号文章的工程化复现。表格里的**标题与链接**逐篇取自本仓库
快照文件头部的「# 标题」「源:」行，未经改写。

- 15 篇来自公众号「**小周谈智驾数据闭环**」（署名在「小周」与「小周的成长之路」之间
  变动过，按原文头部登记），构成系列一 / 系列二 / 系列三 + 一篇全景综述；
- 1 篇（a9）来自 **Apache Paimon PMC 李劲松**，是本项目 VARIANT 落地的外部依据。

原文是**架构设计文章**而非设计文档——完整 DDL、完整规则配置、完整参数表均未公开
（每篇文末都注明需向作者公众号索取完整版）。本项目对留白处的处理一律登记在
[`source-deviations.md`](./source-deviations.md)。

---

## 按时间倒序

| # | 标题 | 公众号 | 日期 | 系列位置 |
|---|---|---|---|---|
| [a1](#a1) | 数据闭环规则挖掘引擎实战：从结构化元数据中批量发现高价值场景 | 小周 | 2026-09-12 | 系列三 · 数据挖掘与 AI 第 4 篇 |
| [a2](#a2) | 数据闭环统一标签体系设计：三来源标签的字典映射与去重治理 | 小周 | 2026-09-11 | 系列三 · 数据挖掘与 AI 第 3 篇 |
| [a3](#a3) | 数据闭环分层抽帧策略从 TB 级采集数据中提取高价值帧：三道成本闸门 | 小周 | 2026-09-10 | 系列三 · 数据挖掘与 AI 第 2 篇 |
| [a4](#a4) | 数据闭环数据挖掘平台架构设计：控制面/数据面分离的工程实践 | 小周 | 2026-09-09 | 系列三 · 数据挖掘与 AI 第 1 篇 |
| [a5](#a5) | 【万字长文】智驾数据闭环的湖仓架构全景：8 环节闭环 × 11 数据域 × 79+ 张表 × 6 大场景 | 小周 | 2026-09-08 | 全景综述特辑 |
| [a8](#a8) | 智驾数据闭环湖仓实战：采集数据的合规入湖链路 | 小周 | 2026-09-07 | 系列二 · 湖仓实战 第 8 篇（收官） |
| [a7](#a7) | 数据闭环湖仓实战 HNSW 向量索引落地 StarRocks：从 Paimon 外部表到语义检索 | 小周 | 2026-09-06 | 系列二 · 湖仓实战 第 7 篇 |
| [a6](#a6) | 数据质量门禁设计：智驾数据入湖的五步校验链路 | 小周 | 2026-09-05 | 系列二 · 湖仓实战 第 6 篇 |
| [a13](#a13) | 智驾数据闭环湖仓实战：Paimon + Neo4j 湖图双引擎数据血缘追溯系统 | 小周 | 2026-09-04 | 系列二 · 湖仓实战 第 5 篇 |
| [a12](#a12) | 智驾数据闭环湖仓实战：数仓命名规范 + 11 数据域划分 + 分区策略全解 | 小周 | 2026-09-01 | 系列二 · 湖仓实战 第 2 篇 |
| [a10](#a10) | 智驾数据闭环湖仓实战： Apache Paimon 分层建模实践 | 小周 | 2026-08-31 | 系列二 · 湖仓实战 第 1 篇 |
| [a14](#a14) | 存储生命周期五级分层：智驾 PB 级数据成本治理实战 | 小周 | 2026-08-30 | 系列一 · 智驾数据闭环 第 7 篇 |
| [a15](#a15) | 11 张 ADS 数据闭环表开箱即用智驾数据产品矩阵全览 | 小周 | 2026-08-28 | 系列一 第五篇 |
| [a11](#a11) | 数据闭环全局 data_id 设计：贯穿智驾全链路的三级 ID 体系 | 小周的成长之路 | 2026-08-27 | 系列一 第四篇 |
| [a16](#a16) | 从痛点到蓝图：智驾数据闭环湖仓架构设计全解 | 小周的成长之路 | 2026-08-26 | 系列一 第三篇 |
| [a9](#a9) | Paimon 2.0 系列：从 JSON 到 Variant，电商与智驾的半结构化实践 | 李劲松 | 2026-08-17 | 外部参考（Paimon 官方） |

> 快照缺口：系列二共 8 篇，本仓库有第 1、2、5、6、7、8 篇；**第 3 篇《双路查询架构》
> 与第 4 篇《三通道入湖》不在这 16 篇内**。这两个主题的实现依据取自 [a5] 第六、七章
> 的综述段落，已在 [`architecture.md`](./architecture.md) 第 6、10 节注明。

---

## 逐篇：内容与对应实现

### a1

**数据闭环规则挖掘引擎实战：从结构化元数据中批量发现高价值场景**
小周 · 2026-09-12 · 系列三 · 数据挖掘与 AI 第 4 篇
<https://mp.weixin.qq.com/s/4dyDxCnttMqkN3GxEoJRRA>

规则即数据（配置也是湖仓资产）· 六大规则种类（标签组合 / 时空地理 / 车辆信号 /
模型输出 / 事件触发 / 多条件复合）· 批流双模执行与调度链路 · 挖掘结果的去向。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/mining/` |
| 入口 | `mining/rules.py`（规则模型与 DSL）· `mining/executor.py`（`BatchRuleExecutor`）· `mining/compiler.py`（批流双模编译） |
| 配套 | `mining/config_sync.py`（规则配置 CDC 入湖）· `mining/watermark.py` · `mining/scoring.py` · `mining/gaps.py`（场景缺口） |
| 表 | `catalog/tables/_mining.py`（挖掘域 13 张） |

---

### a2

**数据闭环统一标签体系设计：三来源标签的字典映射与去重治理**
小周 · 2026-09-11 · 系列三 · 数据挖掘与 AI 第 3 篇
<https://mp.weixin.qq.com/s/YP58mq0QYOATRZUzsC0QLg>

五大类别受控词表 · 三源收口管道（字典映射 → 幂等去重 → 血缘填充）·
四态状态机治理标签从生到死 · 与质量门禁联动（未审核标签进不了训练集）。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/tags/` |
| 入口 | `tags/pipeline.py`（三源收口管道）· `tags/dictionary.py`（受控词表） |
| 配套 | `tags/mapping.py`（管道①）· `tags/dedup.py`（管道②）· `tags/lifecycle.py`（四态）· `tags/gate.py`（门禁联动）· `tags/coverage.py` |
| SQL | `flink/sql/tags_ddl.sql` · `flink/sql/tags_pipeline.sql` |

---

### a3

**数据闭环分层抽帧策略从 TB 级采集数据中提取高价值帧：三道成本闸门**
小周 · 2026-09-10 · 系列三 · 数据挖掘与 AI 第 2 篇
<https://mp.weixin.qq.com/s/RrD59_FPqek-zSFMIdCRKQ>

三道成本闸门：常规抽帧（普查，2 秒 1 帧；原文另给「10 秒 30 帧」的 30fps 原生帧率，
相除得保留 1/60，该比例原文未直接写出）→ 事件抽帧（前 15 后 5 秒
共 20 秒窗口 + 异步补抽）→ 推理抽帧（用打分代替随机选帧）· 四个实现要点。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/sampling/` |
| 入口 | `sampling/engine.py`（`SamplingMode` + 抽帧引擎）· `sampling/gates.py`（三道闸门） |
| 配套 | `sampling/frames.py` · `sampling/scoring.py`（关键帧打分）· `sampling/cost.py` · `sampling/compliance.py` · `sampling/constants.py`（全部数字的唯一出处） |
| SQL | `flink/sql/sampling_00_frame_table.sql` ~ `sampling_03_inference_keyframe.sql` |

---

### a4

**数据闭环数据挖掘平台架构设计：控制面/数据面分离的工程实践**
小周 · 2026-09-09 · 系列三 · 数据挖掘与 AI 第 1 篇
<https://mp.weixin.qq.com/s/bSekzM_WjtGtAd_1WdbBAQ>

挖掘域的枢纽与双出口 · 第一设计原则「平台不持有主数据」· 五层应用架构 ·
控制面/数据面分离 · K8s 三区 + GPU 分时复用 · OpenAPI 出口。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/controlplane/` + `src/adas_lakehouse/dataplane/` |
| 入口 | `controlplane/scheduler.py`（`ControlPlane`）· `controlplane/contracts.py`（两面的边界契约）· `dataplane/execution.py` |
| 配套 | `controlplane/openapi.py` · `controlplane/rules.py` · `controlplane/store.py` · `dataplane/engines.py` · `dataplane/gpu.py`（三区与分时复用）· `dataplane/query.py` |

---

### a5

**【万字长文】智驾数据闭环的湖仓架构全景：8 环节闭环 × 11 数据域 × 79+ 张表 × 6 大场景**
小周 · 2026-09-08 · 全景综述特辑
<https://mp.weixin.qq.com/s/UmHoxjBwRtZT0PgwjkL9DQ>

全系列的收拢篇，11 章：认知框架 → 总体架构 → 三级 ID → 建模骨架 → 六大核心场景 →
三通道入湖 → 双路查询与语义检索 → 治理三件套 → 服务层 → 行业对标 → 收拢。

| 对应实现 | |
|---|---|
| 覆盖面 | 全项目。是 `README.md` 架构图与 [`architecture.md`](./architecture.md) 第 0 节的主要依据 |
| 独有依据 | 第六章（三通道入湖，因系列二第 4 篇不在快照内）· 第七章（双路查询，因系列二第 3 篇不在快照内）· 第九章（六项闭环业务服务 → `ads/products.py`） |
| 偏差 | 第四章的表数（89 张）与本篇标题（79+ 张）自相矛盾，见 [source-deviations A-1](./source-deviations.md) |

---

### a6

**数据质量门禁设计：智驾数据入湖的五步校验链路**
小周 · 2026-09-05 · 系列二 · 湖仓实战 第 6 篇
<https://mp.weixin.qq.com/s/e7lf3LrjX9JMHMvVnu4Anw>

五层质量问题全景 · 六维检查框架 · YAML 配置化规则引擎 + 检查器三分支 ·
分源规则细化 · 五步异常闭环（拦截 → 隔离 → 告警 → 分流处置 → 复验）· 门禁自身的监控与灰度。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/quality/` |
| 入口 | `quality/gate.py`（`GateDecision` / `BatchResult`）· `quality/closed_loop.py`（五步闭环） |
| 配套 | `quality/thresholds.py`（原文全部数字的唯一出处）· `quality/severity.py`（六维 / 三分支 / 四档 SLA）· `quality/rules.py` + `ruleset.py` + `builtin.py` · `quality/isolation.py`（隔离表与重放）· `quality/alerting.py` |
| 配置 / SQL | `quality/rules/quality_rules.yaml` · `flink/sql/quality_gate_pipeline.sql` · `flink/sql/quality_issue_table.sql` |

---

### a7

**数据闭环湖仓实战 HNSW 向量索引落地 StarRocks：从 Paimon 外部表到语义检索**
小周 · 2026-09-06 · 系列二 · 湖仓实战 第 7 篇
<https://mp.weixin.qq.com/s/uXsYl0yOsvKTjgUDojsFew>

三方案对比 · 向量落湖（千万级向量明细表）· Embedding 五步流水线 ·
外部表上建 HNSW（余弦相似度 · 双索引 · 分区级刷新；本篇只说「M / efConstruction
在 POC 阶段按数据规模压测定参」，**落地值 M=16 / efConstruction=200 出自 [a5] 第七章**，
本篇第四章的索引 DDL 在原文里是图片）· 统一检索 API 五步 + 四类检索能力 ·
P95 ≤ 2s 验收线与内表兜底降级路径。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/vector/` |
| 入口 | `vector/search.py`（统一检索 API）· `vector/index.py`（HNSW 建索引） |
| 配套 | `vector/params.py`（索引参数与刷新调度）· `vector/embedding.py`（五步流水线）· `vector/backend.py`（外部表 / 内表双后端与降级）· `vector/versioning.py` · `vector/schema.py` · `vector/render_sql.py` |
| SQL | `ddl/starrocks_vector.sql` · `flink/sql/vector_embedding_pipeline.sql` · `vector_image_vector_detail.sql` · `vector_version_switch.sql` |

---

### a8

**智驾数据闭环湖仓实战：采集数据的合规入湖链路**
小周 · 2026-09-07 · 系列二 · 湖仓实战 第 8 篇（收官）
<https://mp.weixin.qq.com/s/qhddTZf_P_g81s5z1RkPPA>

第三条通道为何走不了 CDC 和 Kafka · 五步合规链路 · 双脱敏机制 ·
合规云架构约束（独立云 + 同 VPC）· 文件外置原则 · 与质量门禁的衔接。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/ingest/` |
| 入口 | `ingest/pipeline.py`（`ComplianceIngestPipeline`）· `ingest/channels.py`（三通道） |
| 配套 | `ingest/compliance.py`（双脱敏与合规标记）· `ingest/oss.py`（大文件外置）· `ingest/gate.py`（门禁衔接）· `ingest/sinks.py` · `ingest/constants.py` |
| SQL | `ddl/starrocks_ingest.sql` · `flink/sql/ingest_cdc_mysql.sql` · `ingest_kafka_event.sql` · `ingest_oss_file_meta.sql` |

---

### a9

**Paimon 2.0 系列：从 JSON 到 Variant，电商与智驾的半结构化实践**
李劲松 · 2026-08-17 · 外部参考（Apache Paimon 官方视角）
<https://mp.weixin.qq.com/s/h97TNksrBw7YLjZElo1sjQ>

半结构化数据的两难：存 JSON 字符串写入轻松但查询要反复解析；拆 STRUCT 读取高效但
上游一改字段全链路跟着演进。VARIANT 是第三条路。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/vector/variant.py` |
| 用途 | 向量元数据列 `vector_meta` 的半结构化落地依据（`vector/__init__.py` 的 docstring 明确标注了这一来源） |

> 唯一一篇非「小周谈智驾数据闭环」系列的文章，作为技术选型依据引用。

---

### a10

**智驾数据闭环湖仓实战： Apache Paimon 分层建模实践**
小周 · 2026-08-31 · 系列二 · 湖仓实战 第 1 篇
<https://mp.weixin.qq.com/s/r9FbicVHlbMWFk5jcPLNvQ>

ODS→DWD→DWS→ADS 四层建模 + 11 数据域划分 · 各层职责与设计要点 ·
Paimon 关键设计决策（主键 / Bucket / Changelog）· **全湖表清单总览（显式表名逐条列出）**。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/catalog/` · `src/adas_lakehouse/domains.py` |
| 入口 | `catalog/registry.py`（88 张表聚合与 `validate_all()`）· `catalog/spec.py`（`TableSpec` 物理策略硬校验） |
| 表定义 | `catalog/tables/_*.py`（每数据域一个模块，共 12 个） |
| 产出 | `ddl/00_catalog.sql` + `10_ods.sql` / `20_dwd.sql` / `30_dws.sql` / `40_ads.sql`（由 `adas-lakehouse ddl-export` 渲染） |
| 偏差 | 本篇是表数量口径冲突的核心来源（正文 ODS 28 / DWD 27 vs 显式清单 32 / 30），另有 bucket 上限与分区表张数冲突，见 [source-deviations A-1 / A-4 / A-5](./source-deviations.md) |

---

### a11

**数据闭环全局 data_id 设计：贯穿智驾全链路的三级 ID 体系**
小周的成长之路 · 2026-08-27 · 系列一 第四篇
<https://mp.weixin.qq.com/s/bxyDkxNgLg7qhkJLqCjd9A>

为什么数据需要「身份证」· 三级 ID 逐级派生 · ID 本身即信息（四条生成规则）·
贯穿 7 个环节 · 重刷场景与版本分支并存 · 血缘如何用上 ID。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/ids/__init__.py` |
| 入口 | 三级 ID 的生成 / 派生 / **反解**；`adas-lakehouse id-demo` 可直接演示 |
| 关联 | `lineage/`（本篇是血缘子系统的第二来源，`lineage/__init__.py` 以 `[a11]` 标注） |

---

### a12

**智驾数据闭环湖仓实战：数仓命名规范 + 11 数据域划分 + 分区策略全解**
小周 · 2026-09-01 · 系列二 · 湖仓实战 第 2 篇
<https://mp.weixin.qq.com/s/aojVFyeM7dtvQZLvJf0TzA>

四段式命名公式 · 11 个数据域（含各域各层表数统计表）· 分区决策三规则 + 分区全景表 ·
Bucket 五档 + changelog-producer 三选一 · 主键三原则 + 系统字段规范 · DDL 实战。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/naming.py` · `domains.py` · `catalog/spec.py` |
| 入口 | `naming.parse()` / `naming.lint()`（四段式解析与偏离审计）· `domains.DataDomain`（11 域 + 前缀）· `TableSpec` 的分区 / bucket / changelog 硬校验 |
| 审计 | `adas-lakehouse catalog-validate --list` · `adas-lakehouse catalog-stats` |
| 偏差 | 本篇是命名规范冲突（27 张表不符合本篇自己的公式）与 11 数据域统计表行列不平的来源，见 [source-deviations A-3 / A-6 / A-7](./source-deviations.md) |

---

### a13

**智驾数据闭环湖仓实战：Paimon + Neo4j 湖图双引擎数据血缘追溯系统**
小周 · 2026-09-04 · 系列二 · 湖仓实战 第 5 篇
<https://mp.weixin.qq.com/s/Yfrk2Z_izzzGSC_BQMsinQ>

为什么血缘必须双引擎（SQL 数得清行，数不清跳）· 三级 ID 是两边的统一语言 ·
三级血缘模型（实体 / 版本 / 运行）· 双链路写入（实时保速度，对账保兜底）·
查询协同（图库找关系、湖仓取明细）· 四条工程护栏。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/lineage/` |
| 入口 | `lineage/query.py`（四个查询方向）· `lineage/graph.py`（Cypher 建图与遍历） |
| 配套 | `lineage/model.py`（三级血缘模型 + 五类节点）· `lineage/sync.py`（双链路写入）· `lineage/resolver.py`（图库定范围 → 湖仓补明细）· `lineage/events.py` · `lineage/constants.py` |
| SQL | `ddl/starrocks_lineage.sql` · `flink/sql/lineage_realtime_sync.sql`（链路二）· `lineage_reconcile_t1.sql`（链路三） |

---

### a14

**存储生命周期五级分层：智驾 PB 级数据成本治理实战**
小周 · 2026-08-30 · 系列一 · 智驾数据闭环 第 7 篇
<https://mp.weixin.qq.com/s/DkQfdlmCh0FXwfKds9yiPA>

三类存储介质 · 五级分层模型（热 / 温 / 冷 / 归档 / 删除）· 流转规则与淘汰纪律 ·
元信息驱动（两张表撑起全部治理决策）· 日级调度闭环 + 四道安全闸 ·
成本看板（240GB 年成本 ¥3,226 → ¥203，降幅约 94%）。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/lifecycle/` |
| 入口 | `lifecycle/scheduler.py`（日级调度闭环 + 四道安全闸）· `lifecycle/tiers.py`（五级分层与阈值） |
| 配套 | `lifecycle/decision.py`（扫描决策）· `lifecycle/policy.py`（保留策略与三重删除确认）· `lifecycle/cost.py`（成本模型与那笔 240GB 的账）· `lifecycle/records.py` + `tables.py`（两张元信息表） |
| SQL | `ddl/starrocks_lifecycle.sql` · `flink/sql/lifecycle_state_sync.sql` · `lifecycle_tables.sql` · `lifecycle_writeback.sql` |

---

### a15

**11 张 ADS 数据闭环表开箱即用智驾数据产品矩阵全览**
小周 · 2026-08-28 · 系列一 第五篇
<https://mp.weixin.qq.com/s/c2IZlxJvV8XVBMNUQyGH0Q>

为什么需要「门面」层 · 11 张表全景（六大业务主题 × 六大业务平台）·
闭环健康度两级下钻 · Badcase 即资产（根因 / 场景 / 难例）·
迭代与上车（版本对比 / OTA / 热力图）· 运营底盘（资产目录 / 存储成本 / 挖掘标签）。

| 对应实现 | |
|---|---|
| 主模块 | `src/adas_lakehouse/ads/` |
| 入口 | `ads/products.py`（11 张表 × 六主题 × 六平台矩阵）· `ads/gateway.py`（统一 API 网关） |
| 配套 | `ads/routing.py`（双路选路）· `ads/materialize.py`（内表物化）· `ads/query.py` · `ads/schema.py` · `ads/constants.py` |
| 表 | `catalog/tables/` 中各域的 ADS 层定义，共 11 张 |

---

### a16

**从痛点到蓝图：智驾数据闭环湖仓架构设计全解**
小周的成长之路 · 2026-08-26 · 系列一 第三篇
<https://mp.weixin.qq.com/s/FgYjFzFEnce-dhuzIsgVUw>

七大痛点 · 建设目标（统一底座 + 全局 ID 贯穿）· 四层架构（应用层 / 数据源层 /
接入层 / 湖仓底座）· 两流合一（数据流向下沉淀、服务流向上赋能）·
服务层贯穿五大能力 · 六大设计原则。

| 对应实现 | |
|---|---|
| 覆盖面 | 全项目的架构蓝图依据；[`architecture.md`](./architecture.md) 第 0 节「总体：四层主栈 + 一条贯穿的服务层」的主要来源 |
| 六大原则落点 | 全局数据 ID → `ids/` · 处理版本化 → `ids/` + `lineage/` · 闭环视角 → `domains.py` · 分层解耦 → `catalog/` · 大文件外置 → `ingest/oss.py` · 数据资产化 → `ads/` |

---

## 本仓库快照

原文抓取快照存放在会话 scratchpad 目录 `wx/`（`a1.md` ~ `a16.md`，同名 `.html` 为原始页面）。
快照**不随本项目发布**，仅供开发期核对；上表的链接是唯一的公开出处。
