# 智能问数：Schema Linking 精排 · 实现级设计文档

> 配套系列第 05 篇。Schema Linking 是智能问数**准确率的命门**——90% 的映射错误发生在这里。
> 本文下钻：多路召回融合、精排 Prompt 工程、召回增强、few-shot 检索策略。

---

## 目录

1. [为什么 Schema Linking 是命门](#1-为什么-schema-linking-是命门)
2. [两阶段架构：召回 → 精排](#2-两阶段架构召回--精排)
3. [多路召回与融合](#3-多路召回与融合)
4. [召回质量增强手段](#4-召回质量增强手段)
5. [精排 Prompt 工程](#5-精排-prompt-工程)
6. [few-shot 检索策略](#6-few-shot-检索策略)
7. [评估与调优](#7-评估与调优)

---

## 1. 为什么 Schema Linking 是命门

Schema Linking = 把用户口语中的实体，精准对应到语义层的**指标、维度、字段、枚举值**。

### 1.1 三类典型错误

```
① 召回缺失   用户说"营收"，库里字段叫"revenue"注释为空 → 根本召不回来
② 召回混淆   "销售额"同时召回 gmv / 实付额 / 退款额 → 选错口径=静默错误
③ 幻觉字段   LLM 凭空编一个不存在的字段 → 下游SQL执行报错或算错
```

### 1.2 为什么不能一步到位

```
❌ 全量塞Prompt：上万字段 → 超长(爆token) + 干扰项多(准确率低) + 慢 + 贵
❌ 纯向量Top1：  相似字段向量太近("下单时间"vs"支付时间") → 频繁选错
✅ 召回+精排：   向量把上万收敛到~20候选(快) + LLM在小集合精判(准)
```

---

## 2. 两阶段架构：召回 → 精排

```
                用户问题 + 抽取实体
                        ↓
   ┌────────────────────────────────────────┐
   │  阶段一：多路召回 Recall                │
   │  ┌──────────┬──────────┬──────────┐     │
   │  │向量召回  │BM25召回  │值索引召回│     │
   │  │(语义)    │(字面)    │(枚举值)  │     │
   │  └────┬─────┴────┬─────┴────┬─────┘     │
   │       └── RRF融合去重 ──────┘           │
   │              ↓ Top-20候选               │
   └────────────────────────────────────────┘
                        ↓
   ┌────────────────────────────────────────┐
   │  阶段二：精排 Rerank                    │
   │  Cross-Encoder粗排(可选) → LLM精判      │
   │  输出：选定映射 + 每项置信度 + unmatched│
   └────────────────────────────────────────┘
```

---

## 3. 多路召回与融合

单一召回有盲区，**向量+字面+值索引三路互补**。

### 3.1 三路召回

```python
class MultiRecall:
    def __init__(self, vector_store, bm25_index, enum_index):
        self.vs = vector_store       # 语义召回（bge等embedding）
        self.bm25 = bm25_index       # 字面召回（补向量对专有名词/缩写的短板）
        self.enum = enum_index       # 枚举值召回

    def recall(self, term: str, unit_types: list, biz_domain=None, k=15):
        # 路1：向量语义召回 —— 抓语义相近（"营收"→"revenue"）
        vec_hits = self.vs.search(term, filter={"unit_type": unit_types,
                                  "biz_domain": biz_domain}, top_k=k)
        # 路2：BM25字面召回 —— 抓精确/缩写（"GMV""SKU""UV"这类）
        bm25_hits = self.bm25.search(term, filter={"unit_type": unit_types}, top_k=k)
        # 路3：枚举值召回 —— term可能是维度值而非维度名（"上海"→city='上海'）
        enum_hits = self.enum.search(term, top_k=5)
        return {"vec": vec_hits, "bm25": bm25_hits, "enum": enum_hits}
```

### 3.2 RRF 融合（Reciprocal Rank Fusion）

不同召回分数不可比，用排名融合而非分数加权：

```python
def rrf_fusion(recall_results: dict, k=60) -> list:
    """RRF: score = Σ 1/(k + rank_i)。对每个文档在各路的排名求倒数和"""
    scores = {}
    for source, hits in recall_results.items():
        weight = {"vec": 1.0, "bm25": 0.8, "enum": 1.2}.get(source, 1.0)
        for rank, hit in enumerate(hits):
            uid = hit["unit_id"]
            scores[uid] = scores.get(uid, 0) + weight * (1.0 / (k + rank + 1))
    # 按融合分排序，去重
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [uid for uid, _ in ranked[:20]]
```

> RRF 的好处：不需要归一化不同召回的分数，鲁棒且免调参。枚举值召回给更高权重(1.2)，因为它命中往往意味着强信号（用户明确说了某个值）。

---

## 4. 召回质量增强手段

召回质量的天花板由**索引文本质量**决定。以下是提升召回率的关键手段。

### 4.1 embedding_text 拼接（决定语义召回效果）

```python
def build_embedding_text(unit):
    """核心：把中文别名和描述拼进去，别只用英文字段名"""
    parts = [
        unit["display_name"],                    # 销售额(GMV)
        " ".join(unit.get("aliases", [])),       # 成交额 营业额 销售金额
        unit.get("description", ""),             # 订单成交总金额含税不含运费
        " ".join(unit.get("usage_examples", [])),# 查销售额 各区域GMV对比
    ]
    return " ".join(p for p in parts if p)

# 反例：只用 unit["name"]="gmv" 向量化
#   → 用户说"销售额"，"销售额"和"gmv"向量距离远 → 召不回
```

### 4.2 注释增强（LLM 辅助补全字段描述）

历史遗留库字段注释往往为空或是拼音缩写。用 LLM + 样本数据自动补：

```python
ENRICH_PROMPT = """根据字段名、样例数据、所在表，推断字段的业务含义，生成中文描述和可能的业务别名。
表名：{table}   字段名：{col}   数据类型：{dtype}
样例值：{samples}
同表其他字段：{siblings}

输出JSON：{{"description": "...", "aliases": ["...","..."]}}"""

def enrich_schema(unit, sample_data, llm):
    if unit.get("description"):
        return unit   # 已有注释不覆盖
    enriched = llm.json(ENRICH_PROMPT.format(
        table=unit["table"], col=unit["name"], dtype=unit["data_type"],
        samples=sample_data[:5], siblings=unit["sibling_columns"]))
    unit["description"] = enriched["description"]
    unit["aliases"] = enriched["aliases"]
    unit["_enriched_by_llm"] = True   # 标记，供人工复核
    return unit
```

> 增强后的描述**必须人工抽检**，LLM 可能推断错业务含义，反而污染召回。

### 4.3 列值索引（解决维度值定位）

```python
# 高基数枚举维度单独建值索引
def build_enum_index(dimension, values):
    docs = []
    for v in values:
        docs.append({
            "unit_id": f"enum.{dimension.name}.{v['code']}",
            "belongs_to": dimension.unit_id,      # 反查用
            "value": v["display"],                 # "上海"
            "aliases": v.get("aliases", []),       # ["沪","魔都"]
            "embedding_text": f"{v['display']} {' '.join(v.get('aliases',[]))} "
                              f"{dimension.display_name}",  # "上海 沪 魔都 城市"
        })
    return docs
# 用户说"魔都的订单" → 召回 enum.city.shanghai → 反查 dim.city='上海'
```

### 4.4 HyDE（假设文档扩展，可选进阶）

召回效果不佳时，先让 LLM 生成一个"假设的字段描述"，用它去召回（比原始短问题更贴近索引文本）：

```python
def hyde_recall(term, vs, llm):
    hypo = llm.text(f"用户想查询'{term}'，描述最可能对应的数据库字段含义（一句话）")
    return vs.search(hypo, top_k=15)   # 用假设描述召回，语义更匹配
```

---

## 5. 精排 Prompt 工程

精排是把召回的 ~20 候选，交给 LLM 做最终选择。**Prompt 质量直接决定准确率。**

### 5.1 精排 Prompt 模板（生产级）

```python
RERANK_PROMPT = """你是数据分析专家。从候选Schema中，为用户查询选定要用的指标、维度、过滤条件。

# 用户查询
{query}

# 抽取的业务实体
{entities}

# 候选指标（只能从这里选）
{candidate_metrics}

# 候选维度（只能从这里选）
{candidate_dimensions}

# 候选过滤字段/枚举值（只能从这里选）
{candidate_filters}

# 规则（严格遵守）
1. 禁止使用候选列表之外的任何字段——这会导致查询失败
2. 每个映射必须给出 confidence(0-1)，理由不明确时给低分
3. 一个业务词可能对应多个候选，选语义最贴合的；拿不准就给低confidence触发澄清
4. 用户实体在候选中找不到合适映射时，放入 unmatched，不要强行编造
5. 注意口径：如"销售额"要区分 GMV(含未付) vs 实付额，依据用户上下文判断
6. 过滤值优先匹配枚举值索引（如"华东"→region枚举值）

# 输出JSON
{{
  "metrics": [{{"term":"销售额","unit_id":"metric.gmv","confidence":0.9,"reason":"..."}}],
  "dimensions": [{{"term":"品类","unit_id":"dim.category","confidence":0.97}}],
  "filters": [{{"term":"华东","unit_id":"dim.region","value":"East","confidence":0.95}}],
  "unmatched": [{{"term":"客户满意度","reason":"候选中无对应指标"}}]
}}"""
```

### 5.2 候选格式化（给足信息但不冗余）

```python
def format_candidates(candidates):
    """每个候选展示：id + 显示名 + 别名 + 口径/描述 + 样例值"""
    lines = []
    for c in candidates:
        line = f"- {c['unit_id']} | {c['display_name']}"
        if c.get("aliases"):
            line += f" | 别名:{','.join(c['aliases'][:3])}"
        if c.get("definition_sql"):        # 指标展示口径，帮LLM辨别
            line += f" | 口径:{c['definition_sql']}"
        elif c.get("description"):
            line += f" | 说明:{c['description']}"
        if c.get("sample_values"):
            line += f" | 样例:{c['sample_values'][:2]}"
        lines.append(line)
    return "\n".join(lines)
```

### 5.3 Cross-Encoder 粗排（候选过多时的中间层，可选）

候选超过 30 个时，先用轻量 cross-encoder 砍到 10 个再喂 LLM，省 token 提精度：

```python
def cross_encoder_rerank(query, candidates, ce_model, keep=10):
    pairs = [(query, c["embedding_text"]) for c in candidates]
    scores = ce_model.predict(pairs)   # 交叉编码，比双塔向量更准
    ranked = sorted(zip(candidates, scores), key=lambda x: -x[1])
    return [c for c, _ in ranked[:keep]]
```

**三级精度递进：** 双塔向量召回(快,粗) → Cross-Encoder(中) → LLM(准,慢)。按候选规模决定用几级。

---

## 6. few-shot 检索策略

给生成层注入**与当前问题最相似的"问题-SQL"样例**，是提升生成准确率最有效的手段之一（DAIL-SQL 等验证）。

### 6.1 样例库结构

```json
{
  "example_id": "ex_001",
  "question": "上个月各区域销售额同比",
  "sql": "WITH cur AS (...) SELECT ...",
  "dsl": {"metrics":["gmv"], "comparison":"yoy", ...},
  "schema_used": ["fact_orders", "dim_region"],
  "analysis_type": "comparison",
  "difficulty": "medium",
  "embedding": [...],
  "source": "human_verified",       // human_verified / user_feedback / bi_report
  "success_count": 12,              // 被复用且用户满意的次数
  "quality_score": 0.95
}
```

### 6.2 相似样例检索（不只看问题相似，要看结构相似）

```python
def retrieve_few_shots(query, dsl, example_store, k=3):
    """双重相似：问题语义 + 分析结构（analysis_type/schema）"""
    # 1) 语义召回候选
    candidates = example_store.search(query, top_k=15)
    # 2) 结构加权：同analysis_type、用到相似schema的样例优先
    for c in candidates:
        struct_bonus = 0
        if c["analysis_type"] == dsl.get("analysis"):
            struct_bonus += 0.15
        overlap = len(set(c["schema_used"]) & set(dsl.get("tables", [])))
        struct_bonus += 0.05 * overlap
        c["final_score"] = c["semantic_score"] + struct_bonus \
                          + 0.02 * c.get("success_count", 0)   # 高频成功样例加分
    candidates.sort(key=lambda x: -x["final_score"])
    return candidates[:k]
```

### 6.3 样例选择的多样性（避免 3 个样例都一样）

```python
def diverse_few_shots(candidates, k=3):
    """MMR：兼顾相关性和多样性，避免选到重复样例"""
    selected = [candidates[0]]
    while len(selected) < k and len(selected) < len(candidates):
        best, best_score = None, -1
        for c in candidates:
            if c in selected:
                continue
            relevance = c["final_score"]
            max_sim = max(_sim(c, s) for s in selected)   # 与已选的最大相似度
            mmr = 0.7 * relevance - 0.3 * max_sim          # 惩罚过于相似
            if mmr > best_score:
                best, best_score = c, mmr
        selected.append(best)
    return selected
```

### 6.4 样例库来源与冷启动

```
种子（冷启动）      人工标注高质量 问题-SQL 对，覆盖各analysis_type
BI报表逆向          已上线报表的SQL → LLM反生成对应问题 → 入库
用户反馈（飞轮）    点赞的查询 + 用户纠正后的正确SQL → 持续扩充
质量衰减            success_count低/长期未命中的样例定期下线，防污染
```

---

## 7. 评估与调优

### 7.1 分阶段评估指标

```
召回阶段：
  Recall@20      正确字段是否在Top20候选里    目标>95%（召回缺失=后面全错）
  召回覆盖率      抽取实体能召回到候选的比例

精排阶段：
  Schema Linking准确率   最终选对表/字段/指标的比例   目标>85%
  幻觉率                 引用不存在字段的比例         目标<1%
  口径混淆率             同名不同口径选错的比例        目标<5%
```

### 7.2 归因分析（错了往哪个方向修）

```python
def diagnose_linking_error(case):
    """错误归因，指导优化方向"""
    if case["correct_unit"] not in case["recalled_candidates"]:
        return {"type": "召回缺失", "fix": "改善embedding_text/补别名/加BM25"}
    if case["chosen"] != case["correct_unit"]:
        if _same_name_diff_metric(case):
            return {"type": "口径混淆", "fix": "精排Prompt强化口径区分/展示definition"}
        return {"type": "精排选错", "fix": "补few-shot/优化Prompt"}
    if case["chosen"] not in case["schema_whitelist"]:
        return {"type": "幻觉字段", "fix": "强化Prompt白名单约束/加L2校验"}
```

### 7.3 调优杠杆优先级

```
杠杆1  补全字段中文注释和别名   —— ROI最高，直接提召回率
杠杆2  加BM25/枚举值召回        —— 补语义召回盲区
杠杆3  精排Prompt展示口径定义   —— 治口径混淆
杠杆4  扩充few-shot样例库       —— 治精排选错
杠杆5  上Cross-Encoder中间层    —— 候选多时提精度
```

---

## 附：Schema Linking 全景

```
   抽取实体("销售额""华东""品类")
         ↓
   ┌──── 多路召回 ────┐
   │ 向量  BM25  值索引│  ← embedding_text质量 / 注释增强 决定天花板
   └──────┬───────────┘
      RRF融合去重 → Top20
         ↓
   Cross-Encoder粗排(可选) → Top10
         ↓
   LLM精排(展示口径+白名单约束+few-shot)
         ↓
   选定映射 + 置信度 + unmatched
         ↓
   低置信 → 澄清 ；高置信 → 交生成层
```

**一句话总结：**
> Schema Linking 的准确率，**上限由召回质量（索引文本+注释+多路）决定，下限由精排（Prompt约束+口径展示+few-shot）保底**。
> 召回缺失是最致命的错（后面再准也白搭），所以先把每个字段的中文别名和注释补好——这是 ROI 最高的一件事。

---

*文档版本 v1.0 · 智能问数系列 05 · Schema Linking 精排*
