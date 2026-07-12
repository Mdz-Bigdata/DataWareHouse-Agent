# DataWareHouse-Agent 智能问数系统 V2.0

DataWareHouse-Agent 是一款基于大模型（LLM）与统一语义层的企业级智能问数（NL-to-SQL）系统。本系统摒弃了单纯依靠大模型直出 SQL 的不稳健架构，采用了业内领先的**语义分层治理、Qdrant 向量知识库 Grounding、多轮 Session 继承以及物理与语义双重安全网闸**的分层架构，能够秒级消除指标二义性，保障在万级表、万级指标场景下的问数准确性与企业级查询安全性。

---

## 🎨 系统架构设计

系统数据链路严格遵循“意图抽取 -> 语义 Grounding -> 动态消歧 -> 语义审计网闸 -> 物理转译执行 -> 解释与可视化”的分层流水线（Pipeline）设计：

```mermaid
graph TD
    A[用户自然语言提问] --> B[Session 历史多轮合并]
    B --> C[Qdrant 向量知识库检索]
    C -->|Top-K 语义召回| D[Lexical Precision Guard 词级消歧]
    D -->|指标与Few-shot Prompt注入| E[LLM 受约束生成 NL-to-DSL]
    E --> F[DSL 级别语义网闸审计]
    F -->|无权/越界/非法阻断| G[网闸审计拦截响应]
    F -->|审计通过| H[DSLCompiler 自动维表关联与时区转换]
    H -->|Doris/ClickHouse/PG/MySQL SQL| I[DBService 物理数据源执行]
    I -->|连接超时/网络故障| J[高可用本地 SQLite 仿真自动降级]
    I -->|执行成功| K[Pandas 载入物理查询数据集]
    J -->|执行成功| K
    K --> L[自然语言解释与动态图表推荐]
    L --> M[前端页面展示渲染]
```

---

## 🚀 核心功能模块

### 1. 统一语义层 / 指标层治理
- **口径治理**：解耦物理表字段，在语义层统一注册 Metrics（指标）、Dimensions（维度）和关联关系，杜绝不同报表“同名不同口径”的指标二义性问题。
- **自动维表 JOIN**：基于提前定义的 `JoinPath`（如：`dws_trade_order_daily.region_id = dim_region.region_id`），在生成物理 SQL 时由编译器自动补全 JOIN 路径，模型无需感知繁琐的物理 JOIN 逻辑。
- **跨时区时序对齐**：支持跨时区字段秒级换算（如：北京时间自动换算为芝加哥时间并补齐时间差值），确保数据趋势分析的时效准确性。

### 2. RAG 向量知识库 (Schema Grounding)
- **Qdrant 内存索引**：基于 `qdrant-client` 的内存（`:memory:`）单例，在启动时动态加载系统语义层元数据与 Few-shot 转换 DSL 问答示例。
- **在线/离线双通道 Embedding**：自适应切换在线大模型 Embedding 接口与本地字符级 N-gram 特征哈希向量生成器，实现离线、零依赖启动的高可用性。
- **Lexical Precision Guard 词法精准防御**：对于“退款量、退款额、退款率”等高重合度的语义候选指标，引入提问别名字串匹配过滤，自动去除近似干扰项，消除口径歧义。

### 3. 多轮会话 Session 状态继承
- 采用 Pydantic 模型 `QuerySessionState` 维护用户多轮问答意图；
- 提问“华东区过去30天交易额是多少”，紧接提问“退款率呢”，系统将自动补全继承上一轮的时间范围（“过去30天”）与区域范围（“华东区”），实现连贯、流畅的多轮复杂会话。

### 4. 双重安全网闸 Guardrails
- **语义审计网闸**：强制拦截未注册指标的非法访问。
- **权限控制网闸**：根据用户角色（如：`user`、`analyst`、`admin`）实施指标级列权限管控（如：普通用户无权访问敏感指标 `refund_ratio`）。
- **性能防刷网闸**：计算查询时间跨度，当发现查询时间范围超出系统最大限制（如：超 365 天）时直接予以拦截，防止低效 SQL 刷死分布式分析引擎。

### 5. 物理多数据源执行与方言转译
- 支持 **MySQL、PostgreSQL、ClickHouse、Doris、StarRocks** 等常规数据库连接；
- 采用 SQLGlot 动态将 Doris 方言 SQL 精准转译为底层对应的物理数据库方言；
- 具备 **HA Fallback 机制**，物理数据库离线或网络超时（2 秒阈值）时，系统自动降级回 SQLite 本地仿真电商数据，保持系统 100% 的稳健性。

---

## 🛠️ 安装部署指南

### 1. 环境依赖安装
确保您已安装 Python 3.10+，并建议在虚拟环境中安装依赖：

```bash
# 进入后端目录并激活虚拟环境
cd backend
python3 -m venv venv
source venv/bin/activate

# 安装大模型、Web 服务、向量库和数据库底层组件
pip install fastapi uvicorn pydantic requests sqlglot pandas numpy sqlalchemy python-dotenv qdrant-client

# 根据连接的常规数据库类型，安装对应驱动：
pip install pymysql            # MySQL / Doris / StarRocks 驱动
pip install psycopg2-binary    # PostgreSQL 驱动
pip install clickhouse-connect # ClickHouse 驱动
```

### 2. 配置文件说明 (两种配置模式)

#### 方式一：.env 环境变量配置（推荐，生产环境首选）
在项目根目录（或 `backend` 目录）下，将 `[.env.example](file:///.env.example)` 文件复制重命名为 `.env`，填入您的真实物理数据库连接信息：

```bash
cp .env.example .env
```

`.env` 变量规范如下：
```bash
# 物理数据库方言类型：可选 mysql / postgresql / clickhouse / doris / starrocks
DB_TYPE=mysql
# 数据库连接串
DB_URL=mysql+pymysql://root:password123@localhost:3306/dw_store?charset=utf8mb4
# 连接池基础配置
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
```

#### 方式二：llm_config.json 文件配置
您也可以在 `backend/llm_config.json` 中配置大模型提供商 API Key 以及 `database` 数据库节点：
```json
{
  "active_vendor": "deepseek",
  "vendors": {
    "deepseek": {
      "api_key": "your_api_key_here",
      "base_url": "https://api.deepseek.com/v1"
    }
  },
  "database": {
    "active_db": "mysql",
    "connections": {
      "mysql": {
        "url": "mysql+pymysql://root:password123@localhost:3306/dw_store?charset=utf8mb4",
        "pool_size": 10,
        "max_overflow": 20
      }
    }
  }
}
```

### 3. 系统启动
您可以使用根目录下自带的启动脚本一键并发拉起前端（React）和后端（FastAPI）服务：

```bash
# 赋予执行权限并启动
chmod +x start.sh
./start.sh
```
服务启动后：
- 后端 API 地址：`http://localhost:8000`
- 前端交互页面：`http://localhost:3000`

---

## 📖 核心模块开发指南

### 1. 指标与维度治理配置 (语义层注册)
若需要添加新的指标（如 `gross_margin`）或新维度，请在 `backend/app/service/semantic_layer.py` 中进行配置：

```python
# 注册指标别名及默认聚合函数
self.register_metric(
    name="gross_margin",
    aliases=["毛利", "毛利率", "利润率"],
    default_agg="formula",
    calculation="SUM(gross_profit) / NULLIF(SUM(gmv), 0)", # 计算公式
    restricted=True,  # 是否是敏感受限指标
    allowed_roles=["admin", "analyst"] # 允许访问的角色
)

# 注册维度表及其 JOIN 路径关系
self.register_join(
    source_table="dws_trade_order_daily",
    target_table="dim_goods",
    join_on="dws_trade_order_daily.goods_id = dim_goods.goods_id"
)
```

### 2. 安全网闸控制 (Guardrail)
在 `backend/app/service/guardrail.py` 中，定义了刚性的查询控制逻辑：
- `TIME_SPAN_LIMIT`：控制最大查询天数（默认限制 365 天内），防止扫描大表引起性能雪崩；
- `ROLE_PERMISSION_MAP`：指标访问的权限阻断黑白名单；
- 非法/未注册指标在第一阶段的 DSL 解析时即会被直接截断报错，绝不将非法输入执行至物理层，防范 SQL 注入。

### 3. 多数据库连接池调优
在 `backend/app/service/db_service.py` 中，我们为 SQLAlchemy 连接池挂载了活性检测与超时熔断：
```python
self.real_engine = create_engine(
    db_url,
    pool_size=pool_size,
    max_overflow=max_overflow,
    pool_pre_ping=True,                # 自动检测活性断开连接重连
    connect_args={"connect_timeout": 2} # 2秒超时防卡死，连接失败时会自动安全回滚至 SQLite
)
```

---

## 📈 离线验证单元测试
我们在项目中集成了一套离线验证单元测试，可用于在无大模型在线 API 与无物理数据库时进行全链路快速校验：

```bash
# 运行离线/Fallback 综合链路测试
python3 backend/app/scratch/test_v2.py
```
若控制台输出全部用例的 Success 状态或预期的审计拦截（Expected Block）信息，说明系统各模块集成完好，随时可以挂载物理库并发布上线！
