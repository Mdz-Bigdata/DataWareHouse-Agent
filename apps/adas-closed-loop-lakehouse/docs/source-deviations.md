# 源文偏差登记（source deviations）

> **这份文档是本项目的诚信底线。**
>
> 本项目是对公众号系列文章（16 篇，清单见 [`articles.md`](./articles.md)）的工程化复现。
> 原文是**架构设计文章**，不是设计文档：它给出了表名、分层、数据域、策略与关键数字，
> 但没有公开完整 DDL、完整规则配置与完整参数表（每篇文末都写着「关注公众号后回复
> ⟪湖仓架构设计⟫ / ⟪湖仓分层设计⟫ 获取完整版详细设计文档」）。
>
> 因此本项目里存在两类「不是原文的东西」：
>
> 1. **原文自相矛盾、本项目不得不选边**的地方 —— 记在第一部分「A. 口径冲突」；
> 2. **原文根本没说、本项目自行设计**的地方 —— 记在第二部分「B. 本项目设计」，
>    代码里逐处以 `⚠️ 原文未明确，本项目设计：` 开头标注，共 **302 处**。
>
> 凡本文档没登记的，都应当能在原文里找到直接依据。**发现登记遗漏，请当作 bug 提。**

被三个模块引用：`naming.py`、`catalog/registry.py`、`ads/constants.py`。

---

## A. 口径冲突：原文自己对不上，本项目选了边

### A-1 ★ 表数量：四套互不自洽的数字

这是全系列最大的一处不自洽。同一套表，原文给出了四组数：

| 出处 | 口径 | 数字 |
|---|---|---|
| [a12] 标题 + 正文 + 结语（出现 5 次） | 概述 | **79+ 张** |
| [a10] 第一章 / 第四章总结表 / 第二、三章小标题 | 分层概述 | **ODS 28 / DWD 27 / DWS 14 / ADS 11** = 80 |
| [a10] 第二章 + 第六章**显式表名清单** | 逐表点名 | **ODS 32 / DWD 30 / DWS 14 / ADS 11** = **87** |
| [a12] 第二章 11 数据域统计表「合计」行 | 汇总行 | ODS 32 / DWD 30 / DWS 14 / ADS 11 = **87+ 张（含质量门禁 1 张）** |
| [a5] 第四章 | 全景综述 | 「全湖 **89 张**表：ODS 33 · DWD 28 · DWS 14 · ADS 11，另有 3 张血缘关系表」 |

三点观察：

- [a5] 自身也不自洽：**标题写「79+ 张表」，正文第四章写 89 张**；且 33+28+14+11 = 86，
  要靠「另有 3 张血缘关系表」才凑到 89。
- [a10] 自身也不自洽：**小标题写「28 张 ODS 表」「27 张 DWD 表」，但同一篇的表名清单
  实际点名了 32 张 ODS、30 张 DWD**。逐条数过，不是估的。
- 唯一在两篇之间互相印证的，是 **87 张**这个数：[a10] 的显式表名清单与 [a12] 的
  11 数据域统计表「合计」行完全吻合（32/30/14/11）。

**本项目取舍：以「显式表名清单」为准。** 理由——表名清单是可逐条核对的硬证据，
概述数字是作者写作时的口头约数。最终 **88 张**：

```
88 = 87（[a10] 显式表名清单 = [a12] 合计行）
   +  1（ods_production_kafka_event，见 A-2）
   = 11 数据域 87 张 + 质量门禁伪域 1 张
```

随时可复核：`make stats`（或 `adas-lakehouse catalog-stats`）会打印
「数据域 × 层级」表并与原文口径逐行对账。

---

### A-2 `ods_production_kafka_event` 只在分区表里出现，表清单漏列

[a12] 第三章「分区全景表」第 6 行给出：

| 表名 | 分区字段 | 类型 | 原因 |
|---|---|---|---|
| ods_production_kafka_event | event_type | 业务字段 | 事件类型数据量差异大 |

但这张表**没有出现在 [a10] 第二章的 ODS 表名清单里**。

**本项目取舍：补入 ODS 层 / 生产域**，因为它在分区全景表里是带完整属性（分区字段 +
分区类型 + 分区原因）的正式条目，不像顺手举的例子；且 [a5] 第六章讲「Kafka 通道承接
产线埋点事件流」，生产域确实需要一张 Kafka 事件 ODS 表。

这也正是 87 → 88 的那一张。`catalog-stats` 的对账段会把它单独标出：

```
差异来源：+1 ods_production_kafka_event（ODS / 生产域）— 仅在原文分区策略全景表中出现，正文表名清单漏列
```

---

### A-3 ★ [a12] 11 数据域统计表：逐域行加总 ≠ 合计行

[a12] 第二章那张表，把每一列的 11 个域行加起来，和它自己的「合计」行对不上：

| 层级 | 11 个域行相加 | 表里写的「合计」 | 差 |
|---|---|---|---|
| ODS | 4+8+4+3+3+2+3+2+1+2+0 = **32** | 32 | ✅ 0 |
| DWD | 1+5+3+2+3+1+2+2+1+**10**+2 = **32** | **30** | ❌ +2 |
| DWS | 0+2+2+1+2+0+1+1+0+2+3 = **14** | 14 | ✅ 0 |
| ADS | 0+1+1+1+2+0+1+1+0+1+2 = **10** | **11** | ❌ −1 |

**DWD 的 +2 有明确来源**：[a12] 那张表把挖掘域 DWD 写成 **10 张**（并在表下「三个要点」
第①条特意强调「挖掘域 DWD 最多（10 张）」），而 [a10] 第三章第③条写的是
「DWD 层在闭环域之外，还承载了**挖掘域的 8 张明细表**」，[a10] 的 DWD 表名清单里
挖掘域也确实只点名了 8 张。**10 − 8 = 2，正好是 DWD 逐域加总 32 与合计 30 的差。**

**ADS 的 −1 也能定位**：[a12] 把**数据资产域的 ADS 写成 1 张**，但同一行的「核心含义」
列写的是「数据集 / **场景标签** / **资产目录**」——按这个描述，[a15] 里归属数据资产域的
ADS 表有两张：`ads_data_asset_catalog`（资产目录）与 `ads_scene_library_summary`（场景库）。
11 这个数在 [a5] / [a10] / [a12] 合计行 / [a15] 四处完全一致，只有 [a12] 的数据资产域
**行**写少了 1。本项目按 [a15] 的 11 张实表登记，数据资产域 ADS 取 2 张。

**本项目取舍：以合计行 + 显式表名清单为准，挖掘域 DWD 取 8 张。** 本项目挖掘域
DWD 的 8 张是：

```
dwd_mining_task_detail        dwd_mining_result_detail
dwd_mining_image_frame_detail dwd_mining_image_tag_detail
dwd_mining_image_vector_detail dwd_mining_data_tag_detail
dwd_mining_tag_dict_detail    dwd_scene_gap_detail
```

#### A-3-1 挖掘域还有第三套数：[a4] 的「共 11 张表（1 ODS + 8 DWD + 1 DWS + 1 ADS）」

[a4] 第二章「五条对齐约定」的「数仓命名规范」行原话：

> 新增表遵循 `{层级}_{挖掘域}_{实体}_detail`，共 **11 张表（1 ODS + 8 DWD + 1 DWS + 1 ADS）**

这与前面两套都对不上，三套并列如下（挖掘域）：

| 出处 | ODS | DWD | DWS | ADS | 合计 |
|---|---:|---:|---:|---:|---:|
| [a12] 第二章 11 数据域统计表 挖掘域行 | 2 | **10** | 2 | 1 | 15 |
| [a4] 第二章 对齐约定 | **1** | 8 | **1** | 1 | **11** |
| **本项目注册表** | 2 | 8 | 2 | 1 | **13** |

**本项目取舍：DWD 取 8（同上，[a10] 显式清单 + [a4] 互相印证），ODS / DWS 取
[a12] 的 2 / 2。** 理由：[a4] 说的是「**新增**表」——挖掘平台明确复用采集域既有的
`dwd_collect_clip_detail`（[a4] 二原话「clip 元数据不新建表」），它数的是平台侧新建量，
口径本就窄于「挖掘域一共有几张表」；而 [a12] 的统计表数的是域内全部表。
两条 ODS（`ods_mining_rule_config` 规则配置 CDC 入湖 + `ods_mining_task`）与两条 DWS
（`dws_mining_efficiency_daily` + `dws_mining_tag_coverage_daily`，后者是 [a2] 第四章点名的
「标签覆盖度日指标表」）在原文里都各有出处，按 [a4] 砍到 1 + 1 会丢表。

代价是本项目的 13 张与 [a4] 的 11 张、[a12] 的 15 张都不等——**主动选择，不是遗漏**。
复核：`make stats` 的「10 挖掘域 mining_ 2 8 2 1 13」一行。

⚠️ 附带说明：原文 11 数据域表只给出**各域各层的表数**，没有逐域给出**表名**。
本项目 `catalog-stats` 打印的逐域明细是本项目的登记口径，**对账只在「分层」粒度成立**
（ODS 33 / DWD 30 / DWS 14 / ADS 11 与显式清单逐层吻合）。这一点 `cli.py` 里也有标注。

---

### A-4 Bucket 上限冲突：16-32 档 vs. 五档最大 16

| 出处 | 说法 |
|---|---|
| [a10] 第五章② | 「ODS 层小表 2-4 个 bucket，**DWD 层核心表 16-32 个 bucket**」；举例 `dwd_collect_clip_detail` 设 16，**`dwd_production_artifact_detail` 设 32**（「产物表是血缘核心，并发读写最高」） |
| [a13] 3.1 **DDL 节选** | 同一张 `dwd_production_artifact_detail` 的建表语句里写死 `'bucket' = '32'`，且 `'changelog-producer' = 'input'` |
| [a12] 第四章 | 「我们实际落地了**五档**」——Bucket 决策表五行：1 / 2 / 4 / 8 / **16**，16 档描述为「超大表 / 高并发写入」，代表表 `dwd_data_production_chain`、`dwd_mining_image_vector_detail` |

**本项目取舍：采用 [a12] 的五档，`dwd_production_artifact_detail` 取 16。**

理由：① [a12] 后出（09-01 vs 08-31 / 09-04）且更具体（给了完整决策表而非经验区间与单表举例）；
② 五档是可硬校验的封闭集合，`catalog/spec.py` 把「bucket ∈ {1,2,4,8,16}」做成建表期
断言，区间式描述无法校验；③ 32 档只出现在 `dwd_production_artifact_detail` 这**一张表**上
（[a10] 的经验区间举例 + [a13] 的 DDL 节选，两处指的是同一张表），[a12] 的五档决策表则覆盖全湖。

代价是 `dwd_production_artifact_detail` 从 32 降到 16，与 [a10] 举例及 [a13] 的 DDL 节选都不符
——**这是本项目主动选择的偏差，不是遗漏**。

#### A-4-1 附带：[a13] 的 DDL 节选还与 [a12] 的 changelog 决策口诀冲突

同一段 [a13] 3.1 DDL 给 `dwd_production_artifact_detail`（**DWD 层**）写的是
`'changelog-producer' = 'input'`；而 [a12] 第四章的决策口诀是
「ODS 从 CDC 来 → `input`；DWD 有 Upsert → `lookup`；DWS/ADS 批量聚合 → `full-compaction`」，
并明说「选错最直接的后果：该用 lookup 的表用了 input，下游拿到的 changelog 缺少 -U 记录」。
而这张产物表在 [a13] 自己的 3.2 里正是典型 Upsert 表（旧产物 `artifact_status=superseded` +
`superseded_by_artifact_id` 就地更新）。

**本项目取舍：取 [a12] 的分层规则，DWD 30 张一律 `lookup`。** 与 bucket 同理——
[a12] 给的是可硬校验的全湖规则（`catalog/spec.py` 按层断言 changelog-producer），
[a13] 给的是单表 DDL 节选；且 [a13] 自己描述的 Upsert 语义要求的正是 `lookup`。
`make stats` 第三段可复核当前分布（input 33 / lookup 30 / full-compaction 25，全部按层）。

当前五档分布（`catalog-stats` 第三段）：

```
bucket  表数  分层分布
     1     2  ods 0 / dwd 1 / dws 0 / ads 1
     2    24  ods 0 / dwd 0 / dws 14 / ads 10
     4    45  ods 33 / dwd 12 / dws 0 / ads 0
     8    10  ods 0 / dwd 10 / dws 0 / ads 0
    16     7  ods 0 / dwd 7 / dws 0 / ads 0
```

附带：[a10] 说「ODS 层小表 2-4 个 bucket」，本项目 ODS 33 张**统一取 4**——原文没有
逐表给 ODS 的 bucket 值，取区间上界保证写入并行度，属 B 类设计。

---

### A-5 分区表张数冲突：「全湖唯一的分区表」vs. 分区全景表 6 行

| 出处 | 说法 |
|---|---|
| [a10] 第三章③ | `dwd_mining_image_vector_detail`「是全湖体量最大的表……也是**全湖唯一的分区表**（按 dt 分区），其余表均以 bucket + 主键 Upsert 管理」 |
| [a12] 第三章 | 「分区全景表」明确列出 **6 张**分区表 |

**本项目取舍：取 [a12] 的 6 张**（同样理由：后出、更具体、可校验）。6 张分区表与
分区字段完全照抄 [a12] 分区全景表：

```
ods_data_file_meta              PARTITIONED BY (file_type)
ods_production_kafka_event      PARTITIONED BY (event_type)
ods_quality_issue               PARTITIONED BY (dt)
ods_vehicle_trigger_event       PARTITIONED BY (trigger_type)
dwd_evaluation_result_detail    PARTITIONED BY (evaluation_type)
dwd_mining_image_vector_detail  PARTITIONED BY (dt)
```

6 张全部满足 [a12] 第五章「原则三：分区表主键必须含分区字段」，由 `catalog/spec.py` 硬校验。

附带：[a5] 第四章还给了第三种说法——「全湖统一采用 **dt（天）为主分区、数据域相关
维度为辅分区**的约定」，即**所有表都分区、且都是二级分区**。这与 [a10]（唯一分区表）、
[a12]（6 张单字段分区）三者互斥。本项目取 [a12]：6 张、单字段分区，其余走
[a12] 第三章「规则三：主键 Upsert + 无明确分区维度 → 不分区」。

---

### A-6 ★ 命名规范自相矛盾：88 张表里 25 张不符合文章自己提出的公式（27 条偏离）

[a12] 第一章立了四段式命名公式：

```
{层级前缀}_{数据域}_{业务实体}_{粒度后缀}
```

并声明其价值是「**表名即文档**。新人入职不需要翻 200 页数仓字典，看表名就知道
80% 的信息」。但原文自己给出的表名清单里，有相当一部分并不满足这个公式。

`make validate`（`adas-lakehouse catalog-validate --list`）的完整审计结论：

```
硬违规                      0 处
命名偏离                    27 条，涉及 25 张表（已登记 27，未登记 0）
  └ dws_scene_distribution 与 dws_data_contribution 同时缺域段与后缀，各计两条

两个口径不同的合规率，不要混用：
  lint 无告警              63/88 (72%)  —— ODS 贴源命名、ADS 面向应用命名，
                                          这两层不强制粒度后缀，故豁免
  严格四段式（域段+后缀全有） 41/88 (47%)  —— 即「表名即文档」承诺真正兑现的比例
  差额 22 张：有域段但无标准后缀，因层级豁免而不告警
            （ods_collect_task / ods_qc_task / ods_dataset_data_list 等）
```

#### A-6-1 缺数据域段（第二段解析不出数据域）：23 张

在 `TableSpec` 上以 `name_omits_domain=True` 登记，**23 张全部已登记，0 张未登记**。
按层分布 `ods 10 / dwd 6 / dws 2 / ads 5`：

| 层 | 表名 |
|---|---|
| ODS (10) | `ods_data_file_meta` · `ods_sensor_config` · `ods_vehicle_info` · `ods_manual_operation_log` · `ods_quality_issue` · `ods_scene_tag` · `ods_model_version` · `ods_vehicle_function_test` · `ods_vehicle_trigger_event` · `ods_vehicle_software_version` |
| DWD (6) | `dwd_data_production_chain` · `dwd_manual_operation_detail` · `dwd_scene_tag_relation` · `dwd_vehicle_trigger_detail` · `dwd_vehicle_software_distribution_detail` · `dwd_scene_gap_detail` |
| DWS (2) | `dws_scene_distribution` · `dws_data_contribution` |
| ADS (5) | `ads_data_asset_catalog` · `ads_scene_library_summary` · `ads_model_version_comparison` · `ads_hard_case_library` · `ads_storage_cost_dashboard` |

其中三张的讽刺意味最强，因为它们正是原文用来**演示命名规范**的例子：
`ods_vehicle_info` 与 `ods_manual_operation_log` 出现在 [a12] 第一章第四段
「粒度后缀」表的示例列里；`dwd_data_production_chain` 是 [a12] 第六章「DDL 实战」
的主角，其「逐条对照本文规则」表格里明写「命名规范：`dwd_` + `production_` +
`data_production_chain`」——但实际表名是 `dwd_data_production_chain`，
**`production_` 域段并不在第二段的位置上**，而是嵌在业务实体里。

#### A-6-2 缺标准粒度后缀：4 张构成告警

第四段不在 13 种标准粒度后缀内的共 35 张，但 `naming.lint` 只对 **DWD / DWS 层强制**
（ODS 贴源命名、ADS 面向应用命名，原文实践中本就不带后缀），故构成告警的是 4 张，
以 `name_omits_suffix=True` 登记，**4 张全部已登记，0 张未登记**：

```
dwd_closed_loop_storage_lifecycle    （lifecycle 不是标准后缀）
dws_scene_distribution               （distribution 不是标准后缀）
dws_closed_loop_efficiency           （efficiency 不是标准后缀）
dws_data_contribution                （contribution 不是标准后缀）
```

不告警但同样无标准后缀的另外 31 张（ODS 26 / ADS 5）：

```
ODS  ods_collect_task ods_data_file_meta ods_annotation_result ods_annotation_task
     ods_argo_workflow ods_production_line_task ods_qc_result ods_qc_task
     ods_quality_issue ods_dataset_data_list ods_dataset_version ods_scene_tag
     ods_model_version ods_training_metric ods_training_task ods_badcase_record
     ods_evaluation_result ods_evaluation_task ods_simulation_result
     ods_simulation_scenario ods_shadow_mode_data ods_vehicle_function_test
     ods_ota_task ods_vehicle_software_version ods_issue_record ods_mining_task
ADS  ads_data_asset_catalog ads_model_version_comparison
     ads_badcase_root_cause_distribution ads_hard_case_library ads_trigger_heatmap
```

#### A-6-3 域段靠别名才解析得出：15 张

原文把评测域的表名域段写成 `badcase_`、部署域写成 `ota_`、生产域写成
`annotation_` / `qc_` / `argo_`、回传域写成 `shadow_`。`naming.DOMAIN_ALIASES`
只收录「这个词根只可能属于这一个域」的无歧义情况，靠猜的一律不放；
**域归属一律以 `catalog.registry` 的显式声明为准**，解析只用于矛盾检测：

```
ods_annotation_result ods_annotation_task ods_argo_workflow ods_qc_result ods_qc_task
ods_badcase_record ods_shadow_mode_data ods_ota_task
dwd_badcase_detail dwd_shadow_mode_detail dwd_ota_deployment_detail
dws_annotation_quality_daily dws_badcase_statistics
ads_badcase_root_cause_distribution ads_ota_deployment_summary
```

最典型的是 [a12] 第一章「实战拆解」第三条，原文自己写：

> `ads_badcase_root_cause_distribution` → `ads_`（应用层）+ **badcase_（评测域）** +
> `root_cause`（根因）+ `_distribution`（分布）

原文在括号里直接承认 `badcase_` 就是评测域——但规范里的域前缀明明是 `evaluation_`。

#### 本项目取舍

**不改表名。** 表名一律照抄原文（表名是读者对得上号的唯一锚点），改名等于伪造原文；
改公式又会让规范失去意义。折中方案是：**表名照抄 + 偏差逐张登记 + 提供审计命令**。

- `naming.lint()` 非阻断，只产出告警；对 ADS 层放宽域段要求（原文实践确实为可读性
  牺牲域段）；
- 每张偏离表在 `TableSpec` 上带 `name_omits_domain` / `name_omits_suffix` 标记，
  漏登记会被 `catalog-validate` 抓出来（当前「未登记 0」）；
- `make validate` 可当 CI 断言跑：**硬违规必须为 0，偏离必须全部已登记**。

---

### A-7 粒度后缀「10 种」实为 13 种

[a12] 第一章第四段的标题写「粒度后缀（**10 种**标准后缀）」，下面的表也确实只有
10 行——但其中 3 行各自塞了两个后缀：`_chain/_trace`、`_info/_config`、`_log/_event`。
**逐个数是 13 个后缀。**

**本项目取舍：`naming.GRANULARITY_SUFFIXES` 收 13 个**，因为校验需要的是后缀本身，
不是表格行数。13 个分别是：
`detail` `daily` `statistics` `summary` `relation` `chain` `trace` `dashboard`
`analysis` `info` `config` `log` `event`。

---

### A-8 ★ 88 张表的**字段定义全部是本项目推断**

这是范围最大的一处偏差，必须单独说清楚：

> **原文只给出了表名、中文语义、所属域、所属层、分区字段、bucket 数、changelog-producer、
> 主键（部分表）；没有公开任何一张表的完整字段清单。**

原文对此是明确交代过的，[a10] 第六章文末：

> 「本篇列出了 79+ 张表的表名与说明，但受限于篇幅，每张表的完整字段定义（DDL）与
> 字段说明未能一一展开。关注公众号后回复⟪…⟫获取…」

[a12] 文末、[a5] 文末同样注明「完整版《智驾数据闭环湖仓架构设计文档》需向作者索取」。
[a12] 第六章唯一展开的一张完整 DDL（`dwd_data_production_chain`）在原文里是一张**图片**，
文字版未公开。

**本项目取舍：88 张表**全部**在 `TableSpec` 上标记 `fields_inferred=True`**
（`catalog-validate` 复核：88/88）。字段依据是原文给出的中文语义、三级 ID 体系
（[a11]）、系统字段规范（[a12] 第五章：ODS 用 `_ingest_time` + `_source_system`，
其余层用 `_ingest_time` + `update_time`）与各子系统文章中零散提到的字段名推断而来。

**`ddl/10_ods.sql` ~ `ddl/40_ads.sql` 可以直接建表跑通，但它们不是原文的 DDL。**
要拿到作者的真实字段定义，请向原公众号索取完整设计文档。

---

### A-9 其他零散偏差

| # | 偏差 | 原文 | 本项目 |
|---|---|---|---|
| 1 | [a5] 提到「另有 3 张血缘关系表」 | 只在第四章括号里出现一次，未点名、未在任何表清单中列出 | 未作为独立表登记。血缘关系落在 Neo4j 侧（[a13]「图库管关系」），湖仓侧的事实与冗余关系字段挂在既有 DWD 表上 |
| 2 | 数据域数量描述 | [a12] 第二章：「**8 个业务环节 + 3 个支撑域**」= 11 | [a5] 第四章同篇写「**10 个业务过程域** + 1 个跨域闭环域」= 11。两种切法结论都是 11，本项目按 [a12] 的域清单登记（域名与前缀逐字照抄），不去调和这两种描述 |
| 3 | 8 环节 vs 10 过程域 | [a5] 开篇：「采集 → 处理交付 → 训练 → 仿真评测 → OTA 部署 → 回传 → 问题分析 → 挖掘，**8 个环节**」；同篇第四章主环列了 10 个域（采集→生产→数据资产→训练→仿真→评测→部署→回传→分析→挖掘） | 8 环节是把「生产/数据资产」合成「处理交付」、「仿真/评测」合成「仿真评测」的结果。本项目两种口径都保留：`domains.py` 登记 11 域，闭环叙事按 8 环节 |
| 4 | 质量门禁表的域归属 | [a12] 表里作为「合计」行的脚注「含质量门禁 1 张」，不属于 11 个域中的任何一个 | 以 `pseudo_domain` 伪域登记，`catalog-stats` 单列一行，这样「11 数据域小计 87」才能直接与原文合计行对账 |
| 5 | ADS 表数与主题分组 | [a5] 第四章说 11 张表「六大主题」；[a15] 第二章归成六组（闭环健康度 2 / 数据质量资产 3 / 资产运营 1 / 模型迭代与上车 3 / 成本治理 1 / 挖掘运营 1 = 11）✅ 自洽 | 照抄 [a15] 的六组归类，见 `ads/products.py` |

---

## B. 本项目设计：302 处「⚠️ 原文未明确」

> **计数口径**：302 是**设计决策**的处数。直接 grep 会得到 303：
> `grep -rn "原文未明确" --include="*.py" src/ | wc -l` → 303。
> 多出的 1 处是 `src/adas_lakehouse/cli.py:508` —— 那是 `catalog-stats` 命令把这条
> 警示**打印给用户看**的字符串，不是一个新的设计决策，故不计入。

原文是架构文章，很多工程细节（枚举值、阈值默认、重试策略、状态机、接口路径、
持久化格式）确实没写。本项目不把这些留白当成可以随便填的空白，而是**每填一处就在
代码里原地标注**，标注格式统一为：

```
⚠️ 原文未明确，本项目设计：<填了什么 + 为什么这么填>
```

复核方式：

```bash
grep -rn "原文未明确" --include="*.py" src/ | wc -l                        # → 303
grep -rn "原文未明确" --include="*.py" src/ | grep -v '/cli\.py:' | wc -l   # → 302
```

303 − 302 = `cli.py` 里那一处，它是对 A-3 的转述（提醒 `catalog-stats` 打印的逐域明细
是本项目登记口径），不是新的设计缺口，故不计入 302。

### B-0 分布总表

| 子系统 | 处数 | 集中在哪几个文件 | 性质 |
|---|---:|---|---|
| [quality](#b-1-quality40-处) | 40 | `builtin.py` 16 · `thresholds.py` 11 | 内置规则的具体判据、阈值默认值 |
| [mining](#b-2-mining37-处) | 37 | `rules.py` 9 · `scoring.py` 8 · `gaps.py` 5 | 规则 DSL 语法、打分权重、缺口判定 |
| [sampling](#b-3-sampling39-处) | 39 | `constants.py` 13 · `scoring.py` 8 · `gates.py` 8 | 抽帧打分维度权重、劣化检测口径 |
| [controlplane](#b-4-controlplane36-处) | 36 | `constants.py` 14 · `contracts.py` 8 | 控制面表结构、接口路径、幂等键 |
| [ads](#b-5-ads27-处) | 27 | `constants.py` 17 · `gateway.py` 4 | 网关认证/限流/审计/错误码 |
| [vector](#b-6-vector23-处) | 23 | `params.py` 10 | 索引刷新调度、对账口径、断点续跑 |
| [tags](#b-7-tags23-处) | 23 | `lifecycle.py` 5 · `dictionary.py` 5 | 四态状态图、候选池 TTL、审核枚举 |
| [ingest](#b-8-ingest21-处) | 21 | `channels.py` 5 · `constants.py` 4 | 传感器枚举、校验细则、链路 SLA |
| [lineage](#b-9-lineage21-处) | 21 | `constants.py` 6 · `model.py` 4 | 图库约束、节点类型边界、Topic 命名 |
| [lifecycle](#b-10-lifecycle14-处) | 14 | `tiers.py` 4 · `decision.py` 3 | 基线介质、决策优先级、字段来源表 |
| [dataplane](#b-11-dataplane14-处) | 14 | `gpu.py` 8 | GPU 分时复用的窗口与配额 |
| [catalog](#b-12-catalog7-处) | 7 | `tables/_production.py` 3 · `tables/_collect.py` 2 | 门禁判据列的度量形式与取值字典 |
| **合计** | **302** | | |

> `cli.py` 里另有 1 处以同样格式出现的标注（全库 `grep` 计数为 303），是对 A-3 的转述
> ——提醒逐域明细是本项目登记口径，不是新的设计缺口，故不计入 302。

以下按子系统列出**典型条目**（非全量；全量以 `grep` 为准）。

### B-1 quality（40 处）

入湖质量门禁。原文给了六维检查框架、三分支处置、五步异常闭环与分源侧重，
但**没给任何一条内置规则的具体判据，也没给隔离表的字段清单**（原文里是一张图）。

- `severity.py` — P0 合规/安全级规则**禁止 OFF、禁止 GREY，只允许 FULL**：原文只说
  「规则本身灰度发布」，没说 P0 是否可灰度；本项目按「合规红线不灰度」定。
- `thresholds.py`（11 处）— 原文给了数字的（如 SLA 四档、告警分级）逐字登记；
  原文没给数字的门槛值集中在此文件，可整体覆盖，业务代码里不写裸数字。
- `isolation.py` — 隔离表 `ods_quality_issue` 的字段清单在原文里是**未公开文字的图片**，
  本项目按「拦得住、找得回、修得好」三要求推断字段。
- `isolation.py` — 原文只说命中硬规则的数据「写入隔离表」，**没规定写入方式**
  （同步/异步、单条/批）。
- `builtin.py`（16 处）— 六维框架下每一维的具体检查器实现判据。

### B-2 mining（37 处）

规则挖掘引擎。原文给了六大规则种类、批流双模与双出口，但**没给规则 DSL 的语法**。

- `rules.py`（9 处）— `event_ts_ms` 列、Flink `MATCH_RECOGNIZE` 的模式写法；
  原文只说「产出的规则等价」，未涉及编译期检查。
- `rules.py` — 车辆信号类规则的实时性：原文只说「车辆信号……CAN 减速度……，准实时」，
  没给窗口与延迟指标。
- `_sqlfmt.py` — 标识符长度上限：对齐常见湖仓引擎的列名长度限制。
- `watermark.py` — 原文只说「基于 `_ingest_time` / `update_time` 水位做增量」，
  **没给水位表的 DDL**，本项目拟了一张。
- `scoring.py`（8 处）/ `gaps.py`（5 处）— 命中打分权重与场景缺口判定阈值。

### B-3 sampling（39 处）

分层抽帧三道闸门。原文给了三道闸门的触发条件、频率与窗口（这些数字全部逐字照抄），
但**打分的维度权重、劣化检测口径没给**。

- `constants.py`（13 处）— 全部自定数字集中在此文件，可整体覆盖。
- `scoring.py` — 关键帧打分默认**三等分**：原文只列了三个维度，**没给权重**。
- `scoring.py` — 原文只说「模糊、过曝、遮挡」三种劣化，没给各自的检测阈值。
- `compliance.py` — 原文说「复用合规链路的脱敏标记」，**没给标记的字面量**；
  采集域 `compliance_status` 的取值枚举原文也没给。

### B-4 controlplane（36 处）

控制面/数据面分离的控制面。原文第四章对控制面的定义只有寥寥数句。

- `constants.py`（14 处）— 原文只说 MySQL 存「规则配置、任务配置与执行状态、审核流
  状态」，**没给表结构**；幂等键在 Redis 里的前缀也是本项目定的。
- `contracts.py`（8 处）— 两面的边界契约；原文没有规定各引擎的 stage 命名。
- `openapi.py` — 标签治理类接口原文**只给了动作名没给路径**。
- `lifecycle.py` — 状态 → 进度百分比的映射（原文只给了进度查询接口）。
- `store.py` — 原文只说「统一登录，权限复用湖仓四级管控」，没给协议。

### B-5 ads（27 处）

ADS 数据产品矩阵 + 统一 API 网关。11 张表与六项业务服务照抄原文，网关细节自定。

- `constants.py`（17 处）— 网关默认 QPS、超时、分页上限等全部集中于此。
- `gateway.py` — 原文只说网关负责「认证」，**没给认证协议**；只说要「审计」，
  **没说落到哪**；**没规定错误码体系**，本项目给了一套常识映射。
- `materialize.py` — 原文只讲 ADS 内表物化的生产链路，**没有离线形态**，
  但服务层必须能在离线环境下自证正确，故补了离线形态。

### B-6 vector（23 处）

HNSW 索引落 StarRocks。索引参数（cosine、M=16、efConstruction=200）与 P95 ≤ 2s
验收线全部照抄原文，调度与运维细节自定。

⚠️ **索引参数的出处要标对**：`M=16` / `efConstruction=200` 只在 **[a5] 第七章**正文出现
（「HNSW 索引建在 StarRocks 外部表上（cosine 距离、M=16、efConstruction=200），按分区增量刷新」）；
子系统主文 **[a7] 正文并没有这两个数**——[a7] 第四章的索引 DDL 在原文里是图片，
第六章只写「索引参数调优：M / efConstruction 在 POC 阶段按数据规模压测定参」。
两篇不矛盾（一个说方法、一个给压测结论），但引用时必须标 [a5]。
落点：`vector/params.py` 的 `HnswIndexParams` 与 `ads/constants.py` 的
`HNSW_M` / `HNSW_EF_CONSTRUCTION`，两处值必须一致（`params.py` 尾部有断言）。
`ef_search`（默认 128）原文两篇都没给，是本项目值。

- `params.py`（10 处）— 原文只说「刷新能否精确到单个分区」是 **POC 验证项之一**，
  未给结论；本项目按可精确到分区实现并标注。
- `params.py` — 档位切换加防抖，避免一次抖动就切档。
- `versioning.py` — 断点续跑：记录已完成的批次序号持久化成小 JSON 文件，
  调度重启后接着跑——**文件存储是本项目定的**。
- `embedding.py` — 原文只说「GPU 空闲时段分批」，**未给时段**。
- `variant.py` — `vector_meta` 用 Paimon 2.0 VARIANT 落地，依据 [a9]（李劲松）。

### B-7 tags（23 处）

统一标签体系。五大类别受控词表、三源收口管道、四态生命周期均照抄原文。

- `lifecycle.py`（5 处）— 原文**只给了四态含义，没给状态图**；本项目补的每条迁移
  边都逐条给了依据。
- `dictionary.py`（5 处）— 原文只点名了 `review_status` 字段本身，**没给枚举值**。
- `constants.py` — 候选标签提审的最小命中次数；候选池滞留 TTL（天），
  超期未提审自动归档。

### B-8 ingest（21 处）

三通道入湖 + 五步合规链路。五步链路与双脱敏机制照抄原文。

- `channels.py`（5 处）— [a8] 用「相机图像 / 激光雷达点云 / IMU / GNSS 传感器数据」
  笼统带过，具体传感器枚举本项目补齐。
- `oss.py` — `object_key` 的字符白名单。
- `gate.py` — [a8] 只说「图像 / 点云文件完整性与可解码性校验」，没给校验细则。
- `compliance.py` — 原文只说文件需「携带『车端脱敏 + 合规云脱密』双合规标记」，
  没给标记的字面量与校验方式。
- `constants.py` — 原文**没有给链路耗时的目标值或 SLA**。

### B-9 lineage（21 处）

湖图双引擎。三级血缘模型、四个查询方向、双链路写入照抄原文。

- `graph.py` — [a13] 只给了 `MERGE` 语句，**没提约束**；但 `MERGE` 在没有唯一约束时
  会退化，本项目补了约束。
- `model.py`（4 处）— 图库只有五类节点（[a13] 二原话：**Clip / Artifact / Run /
  DatasetVersion / Badcase**），**没有 Training / Evaluation / Model 节点**，
  本项目在边界内建模并标注；其中 Badcase 是唯一一个原文没有给出对账事实表的节点
  （[a13] 3.1 的四张元信息表里没有 Badcase 表），本项目把它的事实源定在评测域已登记的
  `dwd_badcase_detail` 上，并给该表补了冗余列 `traced_artifact_ids`，
  见 `model.NODE_SOURCE_TABLES` 的 ⚠️。
- `query.py` — 原文的「影响分析」Cypher 依赖图库上的 `step` 属性，原文未定义该属性。
- `constants.py`（6 处）— 血缘事件 Topic 命名，与既有 KafkaConfig 同风格。

### B-10 lifecycle（14 处）

五级分层存储治理。五级阈值、介质成本系数、三重删除确认、¥3,226 → ¥203 成本账
全部照抄原文。

[a14] 第二章第二张表给的是**五档介质**的相对单价（基准 = OSS 标准存储），
五个数一个不少地落在 `tiers.RELATIVE_PRICE`：

| 介质 | 原文相对单价 | 常量 |
|---|---|---|
| CPFS / NAS | 约 **8–10x** | `RELATIVE_PRICE[NAS] = 8.0` + `NAS_RELATIVE_PRICE_RANGE = (8.0, 10.0)` |
| OSS 标准存储 | **1x（基准）** | `RELATIVE_PRICE[OSS_STANDARD] = 1.0` |
| OSS 低频存储 | 约 **0.5x** | `RELATIVE_PRICE[OSS_IA] = 0.5` |
| OSS 归档 | 约 **0.15x** | `RELATIVE_PRICE[OSS_ARCHIVE] = 0.15` |
| OSS 深度归档 | 约 **0.05x** | `RELATIVE_PRICE[OSS_DEEP_ARCHIVE] = 0.05` |

NAS 是区间，本项目点估计取下界 8.0（保守），完整区间另存
`NAS_RELATIVE_PRICE_RANGE`；另两个相关数字也已落地：
「归档层价格只有热层的 1/50 甚至更低」→ `ARCHIVE_VS_HOT_PRICE_DIVISOR = 50.0`，
第四章案例示例单价（NAS 1.0 / 标准 0.12 / 低频 0.06 / 归档 0.018 元/GB·月）
→ `SAMPLE_PRICE_YUAN_PER_GB_MONTH`。`tiers.verify_price_consistency()` 会把
两套口径互相验算一遍（8.33x / 0.5x / 0.15x / 1:55.6，四项全部自洽）。

**「五级分层」是生命周期档位，不是介质档位——两个维度别混。**
原文的五级分层是 热 H1 / 温 H2 / 冷 C1 / 归档 C2 / **删除 D**（删除级没有介质）；
介质枚举是 `nas / oss_standard / oss_ia / oss_archive / oss_deep_archive` **五个**
（数量恰好也是 5，纯属巧合）；而落表字段 `lifecycle_stage` 的取值域是
`hot / warm / cold / archive / pending_delete / deleted` **六个**（删除 D 在字段上
拆成「待删」与「已删」两态）。代码里三者分别是 `TIERS`（5 条）、`StorageMedia`（5 值）、
`LifecycleStage`（6 值），`tier_of_stage()` 负责把 `deleted` 归回 D 级。

- `tiers.py`（4 处）— **原文未明确基线介质**，本项目取 OSS 标准存储
  （第四章案例的基线含 NAS 双份常驻，口径不同需说明）。
- `decision.py` — 决策优先级：**原文未明确先后**，本项目按「越不可逆越靠后、
  越保护越靠前」定序。
- `decision.py` — `active_preheat_data_ids` 的**字段来源表原文未明确**，
  本项目取自训练域。
- `scheduler.py` — 扫描决策步只把淘汰候选标成 `pending`（映射关系原文未明确）。
- `tables.py` — 原文以「等」省略的保留期条目，按保留期表给中间产物与临时文件补名。
- `records.py` — 补充 `file_size_bytes` / `data_type` / `source_domain` 字段。

### B-11 dataplane（14 处）

控制面/数据面分离的数据面。原文对数据面的定义只有一句话。

- `gpu.py`（8 处）— 原文只提「K8s 三区 + GPU 分时复用」，**分时窗口的起点小时、
  抽样比例、配额全部本项目设计**，每一处单独标注。
- `execution.py` — 骨架默认**同步**执行，因为这样最容易验证正确性。
- `assets.py` — 原文**没讲 `content_hash` 的原料**，本项目用「决定产出内容的输入集」。
- `query.py` — 原文只说「StarRocks 外部表 HNSW」，**没给函数名与距离度量**。
- `engines.py` — 这套接入骨架是本项目定的工程约定，原文只在架构层面提及。

### B-12 catalog（7 处）

表结构的唯一事实源。这一节是「表定义归 catalog、子系统只引用不定义」收口时新增的：
14 条质量门禁规则原先挂在只存在于子系统本地 TableSpec 的列上，注册表里没有这些列——
**列不存在 = 规则读到 NULL = 阈值静默不生效**，是功能缺陷而不是风格问题。
收口的方向是把这些列**并入注册表**（只增不减），并让子系统改为引用注册表。

并入的门禁承载列共 21 列，分布在三个域模块：

| 模块 | 列 | 承载的规则 |
|---|---|---|
| `tables/_collect.py` | `frame_group_modalities` `continuous_frame_loss_rate` `vehicle_type` `time_sync_error_ms` `near_duplicate_similarity` `batch_missing_ratio` `vehicle_desensitized_flag` `cloud_compliance_decrypted_flag` `decodable_flag` `artifact_id` | QG-OSS-001 ~ 012 |
| `tables/_trigger.py` | `pre_trigger_seconds` `post_trigger_seconds` `duplicate_rate` `gps_jump_meters` `vehicle_manufacture_time` | QG-KFK-002 ~ 008 |
| `tables/_production.py` | `qc_result_id` `qc_conclusion` `annotation_source` `human_review_status` `task_status` `prev_task_status` | QG-CDC-001 ~ 006 |

这 21 列里**大多数是原文要求的能力载体**，只是原文没给字段名——
「采集车硬件同步 ≤ ±10ms、量产车软同步 ≤ ±50ms」「连续丢帧率 > 1% 升级告警」
「车端脱敏 + 合规云脱密双标记」「重复率 > 5% 告警」「标注质量准入」都是原文的原话，
列名与类型属于 A-8 已整体登记的「字段定义全部是本项目推断」，不在此处重复计数。

**下面 6 列不同：原文连要求本身都没给到可判定的程度，是本项目补的**，逐处已在
列注释里按统一格式标注：

- `_collect.py` · `near_duplicate_similarity` — 原文只说「近重复抑制」，
  既没说用什么度量也没给判定口径；本项目落成 0~1 相似度标量列，上游算好、门禁只比阈值。
- `_collect.py` · `batch_missing_ratio` — 原文只说「批次到达完整性」，未给统计量形式；
  本项目落成批级缺片比例。它是「应到 vs 实到」的差，**光看收到的记录推不出来**，
  必须由上游随批次喂进 `check_batch(batch_stats=...)`（对比 `duplicate_rate`
  由门禁的 `DuplicateTracker` 自算，无需上游配合）。
- `_trigger.py` · `gps_jump_meters` — 原文只有「定位无跳变」一句定性要求，
  既没给度量也没给米数；本项目落成「相对上一采样点的跳变距离」标量列。
- `_production.py` · `annotation_source` — 原文只说「预标注结果必须人工审核」，
  没给区分来源的字段，更没给取值字典；`manual/auto_label/pretrain_model/llm_prelabel`
  四个取值由本项目枚举。
- `_production.py` · `human_review_status` — 原文只要求「经人工审核」这一动作，
  没说审核状态怎么留痕；本项目落成四态状态列，门禁按 `reviewed`/`approved` 放行。
- `_production.py` · `prev_task_status` — 原文只说「状态流转合法」，既没给状态机、
  也没说前态从哪来；本项目选择在行上冗余一列前态，而不是让门禁回查上一版本
  ——原文第六章要求 `quality_check_duration` < 1000ms，门禁热路径不能挂回查。

> 另一处（第 7 处）是 `tables/_mining.py` 的规则表 owner 列，早于本次收口，
> 与门禁无关。

---

## C. 如何自行复核这份文档

```bash
make validate    # A-6：硬违规 0，命名偏离 27 全部已登记；A-8：fields_inferred 88/88
make stats       # A-1/A-2/A-3：表数对账 + A-4 bucket 五档分布 + A-5 分区表 6 张
grep -rn "原文未明确" --include="*.py" src/ | wc -l    # B：303（含 cli.py 那一处；不含则 302）
```

如果 `make validate` 的「未登记」不为 0，或 `grep` 计数与本文档不符，
**以命令输出为准，并请更新本文档**。
