# 表名分层解析 & 预聚合优先路由：复用还是各自一份？（§7.8 判定记录）

> 结论先说：**保持两份实现，backend 这份是命名规范的超集；用契约测试钉住共享的那部分，
> 不做运行时 import。** 后来者不必重新做这个判断，改动前先读「什么情况下该重新评估」一节。

涉及的四份代码：

| 位置 | 角色 |
| --- | --- |
| `backend/app/service/semantic_layer.py` §0 | 表名 → (layer, domain, subject, grain) 解析 |
| `backend/app/service/semantic_layer.py::SemanticLayer.route_primary_table` | 预聚合优先选表 |
| `apps/data-agent-engine/backend/core/table_naming.py:11-30` | 表名 → (layer, domain, subject) |
| `apps/data-agent-engine/backend/core/translator.py:239-267` (`_select_table_multi`) | 预聚合优先选表 |

## 1. 为什么不能直接复用引擎那份

**(a) 物理上不可 import。** `apps/data-agent-engine` 是独立发布的应用：有自己的依赖集、
自己的 `provenance/data-agent-engine.sha256` 校验，不在 backend 的 import path 上，
也不是 backend 的依赖。把它变成 backend 的运行时依赖 = 把一个独立应用的版本节奏绑上来，
代价远大于 30 行解析器。

**(b) 更要命的是输入不同，不只是代码不同。**
引擎的选表算法读的是**人工策展的 Ontology YAML**：每张 `source_table` 上声明了
`authority`（gold/silver）、`granularities`、`pre_aggregated`（指标名 → 物理列）、
`status`、`perm_column`、`joinable`、`available_dims`。
backend 的语义层是 `_auto_discover_and_register_schemas()` 从 `information_schema`
自动发现出来的：**上面那些字段一个都没有**，只有表名、列名、列类型。
所以 backend 必须从表名推断分层与粒度——而引擎的 `table_naming.py` 刻意不解析粒度
（粒度由 YAML 的 `granularities` 声明）。两边解的不是同一道题：
引擎是「在已声明的元数据里挑最权威的表」，backend 是「在裸 Schema 里推断哪张表像汇总表」。

**(c) 可复用的东西是"命名规范"这份数据，不是代码。** 前缀集合与粒度后缀含义是约定，
已在 backend 侧对齐为引擎的超集，并用测试锁住不许漂移（见第 3 节）。

## 2. 逐条行为差异登记

### 表名解析

| # | 场景 | engine `table_naming.parse_table_name` | backend `semantic_layer.parse_table_name` | 谁对 |
| --- | --- | --- | --- | --- |
| D1 | `LAYER_PREFIXES` | `ODS/DWD/DWS/ADS/DIM`（5 个） | 多 `DWT`、`DM`（7 个） | backend。引擎解析 `dm_trade_gmv` 得到 `("", "dm", "trade")`——业务域错位成 `dm`，`dwt_user_active_di` 同理。**需要引擎侧补齐前缀**（见第 4 节）。 |
| D2 | 空段 `__a` | `("", "", "")`（`split` 保留空串，首段 `''` 当 domain） | `("", "a", "")`（先过滤空段） | backend |
| D3 | `None` / 非字符串入参 | `AttributeError` | `str(table or "")` 兜底返回 `("","","")` | backend |
| D4 | 粒度后缀 `_di/_1d/_monthly` | 不解析（由 YAML `granularities` 声明） | `parse_table_grain` 映射到 `day/hour/week/month/quarter/year` | 各自对：引擎有策展元数据，backend 没有 |
| — | `dwd_ord_order_di` / `dim_store` / `articles` / `ODS_LOG_X` / `''` | 二者一致 | 同左 | 契约测试覆盖此交集 |

### 预聚合优先选表

| # | 维度 | engine `_select_table_multi` | backend `route_primary_table` |
| --- | --- | --- | --- |
| R1 | 「是不是汇总表」怎么判定 | 表声明了 `pre_aggregated[指标名]` 才算 | 分层前缀 ∈ `ADS/DM/DWT/DWS` 就算（只能推断） |
| R2 | 权威度排序 | `authority != "gold"` 优先，其次 `layer not in ("ADS","DWS")`——**authority 压过分层**，且 ADS 与 DWS 同档 | 无 authority 元数据；用 6 档 `_LAYER_RANK`：`ADS 0 < DM/DWT 1 < DWS 2 < DIM 3 < DWD 4 < ODS 5 < 未知 6` |
| R3 | 同分排序 | `min()` 取 YAML 中的先出现者（隐式依赖文件顺序） | 显式三级 tie-break：同业务域优先 → 列数更少（更贴合的汇总表）→ 表名字典序，**结果确定** |
| R4 | 粒度校验 | `gran in granularities or ("day" in granularities and gran ∈ {week,month,quarter,year})`；**未声明粒度 = 拒绝**；hour 表不能服务 day | `_grain_compatible`：候选粒度 ≤ 请求粒度；**未声明粒度 = 放行**；hour 表可服务 day | 
| R5 | 维度覆盖 | 支持 `joinable` 明细表 + `onto.reachable` 多跳 JOIN 兜底 | 要求所有度量列与维度/过滤列**直连存在于候选表**，缺一列就退回原表（更保守） |
| R6 | 度量列 | 允许公式（`_compile_formula` 编译到 `field_mapping`） | 只允许裸列名；口径含函数/运算时直接退回原表（跨表平移公式不安全） |
| R7 | 权限列 | 有 `perm_rids` 时要求候选表有 `perm_column` | 无此概念，由 `guardrail` 统一管 |
| R8 | 状态路由 | 只跳过 `deprecated`，**不跳过 `draft`**（§7.9-5 缺陷，见第 4 节） | 表级无 status 元数据；对应口径是**指标口径版本状态**，见下 |

R4 的选择说明：backend 若采用引擎的「未声明粒度 = 拒绝」，自动发现出来的表绝大多数没有
粒度后缀，预聚合路由将永远不触发——这个严格性只有在有策展元数据时才成立。
backend 的放行策略配合 R5/R6 的严格列覆盖检查，风险可控。

### §7.9-5 在 backend 侧的落点

引擎的 draft 缺陷在表级（`translator.py:248` 只 `continue` 掉 `deprecated`）。
backend 没有表级 status，同一条规则落在**指标口径版本**上，已在本轮实现：

- `METRIC_STATUSES` 增加 `draft`，`PRODUCTION_METRIC_STATUSES` 只含 `active`；
- `register_metric()`：只有草稿版本的指标**不进 `self.metrics`**——那是 LLM 提示词、
  推荐与检索看到的「在用指标」清单，进了就等于对生产可见；版本台账仍保留；
- `resolve_metric_version()`：草稿不参与默认解析；**显式指定 version 是唯一取用入口**
  （即 §7.9-5 的"除非显式请求"）；
- `DSLCompiler` 编译到只有草稿口径的指标时抛 `DraftMetricNotPublished`，
  报错文案说清「是草稿不是缺失」，避免使用者另造一个同名口径；
- `publish_metric_version(name, version)`：draft → active 的人工治理动作，
  自动发现/探索流程不得代劳。

## 3. 一致性怎么保证（不 import，也不许漂移）

`backend/tests/test_s7_reuse.py::TestEngineParserConformance` 用
`importlib.util.spec_from_file_location` 按**文件路径**加载引擎的 `table_naming.py`
（仅测试期，不产生运行时依赖；引擎目录不存在时 skip），对二者都定义良好的输入断言输出一致，
并把 D1/D2/D3 作为**已登记的预期差异**显式断言——哪天有人改了任何一边，测试会指着差异表报错，
而不是等到线上业务域错位才发现。

## 4. 需要引擎侧配合的事（不在 backend 可改范围内）

1. `core/table_naming.py:8` 的 `LAYER_PREFIXES` 补上 `DWT`、`DM`，否则 `dm_*` / `dwt_*`
   表的业务域解析错位（D1）。补齐后契约测试里对应的「预期差异」断言要同步放宽。
2. `core/translator.py:248` 的状态路由改成跳过 `status in ("deprecated", "draft")`
   （R8 / §7.9-5）。
3. `core/executor.py:27` 的 `MAX_ROWS=1000` 已经带 `truncated` 标志返回，但需确认
   **调用方把这个标志写进了给用户的答案**——只返回不声明等于静默截断（§7.9-6）。

## 5. 什么情况下该重新评估这个结论

- backend 也开始维护策展元数据（表级 authority/granularities/status 落库）→
  那时两边的输入一致了，应抽公共包（例如 `packages/warehouse-naming/`）真合并；
- 引擎被拆成可安装的 Python 包并纳入 backend 依赖 → 解析器可直接复用，路由仍需各自保留；
- 命名规范本身变更（新增分层前缀/粒度后缀）→ 改**两处**，并同步本文第 2 节的差异表。
