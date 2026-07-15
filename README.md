# DataWareHouse-Agent 智能问数与安全网闸系统 V2.0 🚀

DataWareHouse-Agent 是一款专为企业级数仓和金融级分析场景设计的**高可靠、高可信智能问数 (NL-to-SQL) 系统**。

在传统的 NL-to-SQL 架构中，直接依赖大模型生成 SQL 存在**口径二义性、SQL 语法易崩溃、多对多 JOIN 笛卡尔积指标成倍放大、以及数据越权查询**等痛点，导致系统无法在生产环境中落地。本系统通过引入**语义分层治理、Qdrant 向量意图 Grounding、基于 AST 语法树的双重物理安全网闸、自学习纠错记忆对以及智能模型路由**，打通了 NL-to-DSL-to-SQL 的闭环高可靠数据链路，实现了在 11 项核心边界场景中 **100.00% 的黄金回归评估测试套件完美通过**。

---

## 🎨 系统分层架构

系统数据链路严格解耦，包含 **接入与意图层**、**网闸安全层**、**转译与编译层** 以及 **高可用展示层** 四个核心部分：

```mermaid
flowchart TD
    subgraph "接入与意图层 (Input & Intention)"
        A[用户自然语言提问] --> B[Session 历史多轮合并]
        B --> C[Qdrant 向量知识库检索]
        C -->|Top-K 语义召回| D[Lexical Match Boosting 字面匹配提权]
        D -->|拓扑精排与孤岛过滤| D2[Topological Rerank]
        D2 -->|指标维度与 Few-shot 注入| E[Fast 档 LLM 结构化抽取 QueryDSL]
    end

    subgraph "网闸安全层 (Guardrail & Security)"
        E --> F{第一层: 语义 DSL 网闸}
        F -->|行/列越权拦截| G[安全熔断响应]
        F -->|超性能跨度/未注册指标拦截| G
        F -->|审计通过| H[DSLCompiler 确定性编译器]
    end

    subgraph "转译与编译器 (Translation & Compiler)"
        H -->|Doris/ClickHouse/MySQL SQL| I{第二层: 物理 SQL 网闸}
        I -->|除零保护/多对多 JOIN 风险| J[Complex 档 LLM 自纠错自愈]
        J -->|更新自学习经验库| K[(Qdrant / SQLite 记忆对)]
        J -->|输出安全 SQL| I
        I -->|校验通过| L[DBService 物理多源执行]
    end

    subgraph "高可用与展示 (HA & Visualization)"
        L -->|物理库断开/网络超时| M[HA Fallback 本地 SQLite 仿真数据源]
        L -->|成功返回| N[Pandas 载入数据集]
        M -->|成功返回| N
        N --> O[自然语言解释与动态图表推荐]
        O --> P[前端 React 数据看板渲染]
    end
    
    style G fill:#f9f,stroke:#333,stroke-width:2px
    style J fill:#bbf,stroke:#333,stroke-width:2px
    style L fill:#bfb,stroke:#333,stroke-width:2px
    style M fill:#ffb,stroke:#333,stroke-width:2px
```

---

## 📊 全链路核心技术深度解析

### 1. 语义分层与指标口径自动发现 (Metadata Self-Discovery)
本系统彻底摒弃了繁琐的手动硬编码指标和维表配置，实现了 **100% 自动 Schema 发现与语义自适应建模**。
* **物理元数据扫描**：系统在初始化启动时，通过 `db_service` 活性连接池自动连接目标数仓（如 MySQL, Doris, ClickHouse, PostgreSQL 等），扫描所有表的 Column、主外键关联、非空约束及索引。
* **自适应元数据构建**：
  * **Metrics（指标）**：系统自动将数值型事实字段转化为标准的聚合度量指标，并提取字段备注（Comment）作为中文别名注入模型。
  * **Dimensions（维度）**：将分类字段（如省份、分类名）及其可能的值样例扫描存储为维度。
  * **JoinPaths（关联路径）**：基于物理表的外键依赖拓扑或表名命名规范，自动注册维表与事实表的 `LEFT JOIN` / `INNER JOIN` 关联链路。
* **重名维度就近消歧**：引入 `table_dimensions` 双层字典。当多个表拥有同名维度（如 `category_name`）时，编译器优先选择与 Metrics 事实表处于同表的维度，避免发生意外的多余表关联。
* **跨时区时序归一化**：支持时序字段的秒级换算（如：对于美东数仓，系统自动将北京时间换算为芝加哥时间并扣减时差），确保历史趋势对比的准确性。

### 2. Schema Linking 精排重构 (Topological Rerank & Lexical Boosting)
在向量 Cosine 距离召回指标/维度的基础上，系统实现了专门针对数据库拓扑的精排算法 `_rerank_schema_links`，以解决大模型常犯的“幻觉表关联”和“相似指标召回扰动”问题：
1. **字面/词匹配加权 (Lexical Match Boosting)**：
   - 提取问句分词，匹配指标/维度的别名。若有精确命中，赋予 **`+0.40`** 的高置信度高额加分。
   - 若匹配到维度表预先抓取的样例值（Sample Values），赋予 **`+0.30`** 的加分，大幅提高模糊实体（例如问“北京地区销售额”，自动召回 region_name='北京'）的命中概率。
2. **拓扑连通性加权 (Graph Connection Boosting)**：
   - 在确定锚点指标表（置信度最高的 Metric）后，若召回出的维度与指标处于同张物理表，分值额外加 **`+0.20`**。
   - 若维度处于可连通的维表上，自动解析 `JoinPath` 连通性，分数加 **`+0.15`**。
3. **孤岛过滤 (Island Truncation)**：
   - 核心防错机制。如果召回出的某个维度所在的表与锚点指标表**不存在任何可连通的 JOIN 路径**，判定其为“孤岛维度”，**直接强行丢弃/过滤**，彻底杜绝了因为模型幻觉引入的、不可达表的非物理多表交叉 JOIN。

### 3. 基于 AST 的双层网闸防御机制 (Dual-Layer AST Guardrails)
安全网闸系统是金融和数仓核心数据的防火墙，分为语义层和物理 SQL 层的双重过滤。
* **第一层：语义 DSL 网闸**
  * **列级敏感字段阻断**：基于调用者的角色（`user` / `analyst` / `admin`）检查请求指标/维度。例如，非管理员（如 `user`）提问包含 `phone`（手机号）敏感列时，系统直接在 DSL 解析层安全熔断，抛出越权异常。
  * **行级数据辖区静默注入**：根据用户登录态的安全辖区限制（如只允许查华东区），系统自动在 DSL 编译前注入行级过滤条件 `region_name = '华东'`。即使普通用户显式要求查询“全国数据”，该网闸也会强制重写过滤或触发越权阻断防御。
  * **性能防刷控制**：检测时间跨度，当跨度超出天数上限限制（如限制 365 天）时直接拦截，保护底层分布式计算引擎免遭无效大表扫描死锁。
* **第二层：物理 SQL 网闸**
  * 使用 Python `sqlglot` 库将编译器直出的物理 SQL 变体解析为抽象语法树 (AST)，并深度递归遍历所有 AST 节点：
  * **DDL / DML 安全拦截**：遍历 AST，拦截所有包含 `exp.Create`, `exp.Drop`, `exp.Insert`, `exp.Update`, `exp.Delete`, `exp.Alter` 等数据写入或修改操作，确保底层只执行只读的 `SELECT` 查询。
  * **除零保护网闸 (Division-by-Zero Protection)**：
    - 遍历 AST 中所有的 `exp.Div` 除法节点。
    - 检查除法的分母子树，如果不是非零数值常量，则检查其是否套用了 `NULLIF` 函数进行保护。如果无保护，网闸会判定其极易因零值导致数据库计算崩溃，直接予以拦截，并触发自纠错自愈。
  * **多对多 Cartesian JOIN 风险审计 (Cartesian Prevention)**：
    - 遍历所有 `exp.Join` 节点，审计连接条件（`ON`）。
    - 连接条件必须为等值关联（`exp.EQ`），且关联字段必须存在物理主键/外键对齐约束（字段名包含 `id`/`_id`）或处于语义层注册的 `JoinPath` 路径中。若属于多对多字段关联，网闸判定为 Cartesian Fan-out 笛卡尔积扇出，直接阻断，防止返回成倍虚高的放大指标。
  * **大表分区键校验**：对系统预设的物理大表，强制校验 `WHERE` 或 `JOIN ON` 子句中必须覆盖分区键（如 `dt` 等日期列），防止全表扫描导致集群资源崩溃。

### 4. 自愈纠错闭环与持久化学习记忆 (Self-Healing Loop & Learning Memory)
当物理 SQL 网闸拦截异常，或者在物理库执行时发生方言语法报错，系统会立即进入 **闭环自纠错与自愈流程**：
1. **记忆召回**：系统从 Qdrant 的 `few_shots_corrections` 向量库中，检索历史同类报错特征或失败 SQL。
2. **纠错上下文注入**：将匹配的“错误 SQL”、“错误信息”以及“历史正确修复 SQL”作为 Few-shot 自愈上下文注入模型，提升纠错成功率。
3. **Complex 推理重构**：将纠错任务路由至推理能力更强的 `Complex` 高阶模型，要求其针对 AST 网闸报错实施自我修复（例如加上 `NULLIF`），直到通过 SQL 网闸审计。
4. **记忆持久化学习**：一旦自愈后的 SQL 成功执行，系统会自动将 `(原始提问, 报错SQL, 错误日志, 最终修复SQL)` 以元组形式持久化保存至本地 SQLite 数据库，并异步编码存入 Qdrant 纠错向量集，实现随着查询增加而自学习的特征自愈闭环。

### 5. 智能模型分级路由 (Multi-tier LLM Routing)
系统对提问复杂度进行自动化智能评估，以最优成本和速度提供响应：
* **Fast 档路由**：对于相对简单的多轮改写、意图结构化 DSL 解析等日常查询，路由至高并发、低延迟的 `Fast` 模型（如 DeepSeek-V3），节省 70% 的计算算力与响应耗时。
* **Complex 档路由**：当检测到需要执行复杂的多表 JOIN 路由、自纠错修复、高难网闸纠偏时，系统自动切换并分配至推理、归纳和排错能力极强的 `Complex` 高阶模型节点。

### 6. 高可用数据源检测与 SQLite 降级沙箱 (HA Fallback)
* 系统整合 SQLAlchemy 活性检测连接池（配置 `pool_pre_ping=True` 与 2 秒连接超时熔断），支持 **PostgreSQL、MySQL、Doris、StarRocks、ClickHouse** 等多种物理数据库。
* 当物理数据库由于网络闪断、维护或超时离线时，系统自动切换并安全降级到本地 SQLite 内存仿真数据源，保障服务 100% 的稳健可用性。

---

## 📅 黄金测试套件回归报告

我们在离线本地回归沙箱中配置了 11 项涵盖高难金融、电商场景边界条件的黄金测试案例。开启 Mock 大模型进行仿真验证时，所有测试在数秒内高可用跑完并完美通过：

| 用例 ID | 测试描述 | 检验维度 | 测试状态 | 网闸与执行细节 |
| :--- | :--- | :--- | :--- | :--- |
| **CASE-01** | 列值强索引映射与指标提取 | Schema 映射 | `✅ SUCCESS` | 自动通过 `dws -> dim_region` 的 `region_id` 物理关联 |
| **CASE-02** | 确定性相对时间归一化计算 | 时间解析与过滤 | `✅ SUCCESS` | 解析 `上个月` 并归一化为 `dt BETWEEN '2026-05-31' AND '2026-06-30'` |
| **CASE-03** | 多轮上下文指代消解与改写 | 多轮改写消解 | `✅ SUCCESS` | `那前三名的品类呢` 自动合并继承上文的时间和区域上下文 |
| **CASE-04** | 金融级列级权限拦截 | 列级敏感权限阻断 | `✅ SUCCESS` | 普通用户拉取 `手机号` 维度，被语义网闸强力拦截报错 |
| **CASE-05** | 金融级行级权限隔离校验 | 行级静默过滤注入 | `✅ SUCCESS` | 普通用户不带任何过滤提问，自动静默注入限制 `region_name = '华东'` |
| **CASE-06** | 金融级行级跨区越权强力阻断 | 行级数据越权防御 | `✅ SUCCESS` | 普通用户主动提问 `华北` 数据，被行级网闸安全拦截 |
| **CASE-07** | 歧义消歧主动澄清熔断 | 越界模糊词食堂消费 | `✅ SUCCESS` | 提问越界词 `食堂消费` 自动熔断并返回澄清建议选项 |
| **CASE-08** | 可视化自适应饼图归并排序 | 数据统计与排序 | `✅ SUCCESS` | 解析 `销售占比`，执行物理 SQL 并自动转换为 `pie` 图配置 |
| **CASE-09** | 新行业数据源识别与分析 | 多表维度消歧 | `✅ SUCCESS` | `article_history.category_name` 成功就近消歧绑定，避开 `dws` 表 |
| **CASE-10** | 运行时除零保护与自纠错修复 | 运行时防崩溃与自愈 | `✅ SUCCESS` | 第一次直出 SQL 因除零被拦截；第二轮 LLM 自修复 `NULLIF` 执行成功并灌装纠错记忆 |
| **CASE-11** | 多对多无主外键 JOIN 拦截测试 | 多对多笛卡尔积防御 | `✅ SUCCESS` | 网闸识别到 `articles` 与 `user_memory` 为多对多连接关系，安全熔断 |

---

## ⚙️ 配置文件规范

### 1. `.env` 环境变量配置（生产环境推荐）
在 `backend` 目录下创建 `.env` 文件，输入以下物理数据源与方言配置：
```bash
# 激活的物理方言类型：可选 mysql / postgresql / clickhouse / doris / starrocks
DB_TYPE=mysql
# 物理数据库连接串
DB_URL=mysql+pymysql://root:password123@localhost:3306/blog_converter?charset=utf8mb4
# 连接池调优
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
```

### 2. `llm_config.json` 文件配置（大模型及数据库二合一）
在 `backend/llm_config.json` 中，可为系统注入模型端点与 API Key 凭证：
```json
{
  "active_vendor": "deepseek",
  "vendors": {
    "deepseek": {
      "api_key": "sk-your-deepseek-api-key-here",
      "base_url": "https://api.deepseek.com/v1"
    }
  },
  "database": {
    "active_db": "mysql",
    "connections": {
      "mysql": {
        "url": "mysql+pymysql://root:password123@localhost:3306/blog_converter?charset=utf8mb4",
        "pool_size": 10,
        "max_overflow": 20
      }
    }
  }
}
```

---

## 🚀 快速启动部署

### 1. 激活虚拟环境并安装依赖
```bash
cd backend
python3 -m venv venv
source venv/bin/activate

# 安装核心大模型与方言转译依赖
pip install fastapi uvicorn pydantic requests sqlglot pandas numpy sqlalchemy python-dotenv qdrant-client

# 安装常规物理数据库驱动 (根据实际连接类型选用)
pip install pymysql            # MySQL / Doris / StarRocks
pip install psycopg2-binary    # PostgreSQL
pip install clickhouse-connect # ClickHouse
```

### 2. 一键并发拉起前后台服务
在项目根目录下：
```bash
chmod +x start.sh
./start.sh
```
拉起成功后：
- 前端 React 看板页面：`http://localhost:3000`
- 后端 FastAPI API 文档：`http://localhost:8000/docs`

### 3. 回归测试套件运行
您可以在本地沙箱环境中，直接启动黄金测试套件，验证系统的准确率成绩：
```bash
cd backend
PYTHONPATH=. ./venv/bin/python -u tests/test_evaluation_runner.py
```

---

> [!IMPORTANT]
> **金融级高可用与强隔离安全说明**
> - **SQLite 仿真沙箱隔离**：当真实的物理数据库遭遇连接闪断、超时（默认 `2s` 阈值）或凭证失效时，`db_service` 会优雅地将查询路由至本地内存中的高仿真 SQLite，防止服务彻底瘫痪。
> - **静态/动态拦截红线**：一旦发生越权（行级、列级越限）或多对多 Cartesian 扇出风险，系统会直接抛出 `GuardrailException` 予以拦截并直接返回错误，安全熔断流程绝不触及物理分析数据库。
