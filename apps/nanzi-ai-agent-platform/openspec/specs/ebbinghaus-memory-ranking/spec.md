# ebbinghaus-memory-ranking Specification

## Purpose
TBD - created by archiving change memory-ebbinghaus-consolidation. Update Purpose after archive.
## Requirements
### Requirement: Ebbinghaus Retrievability Calculation
系统在召回长期记忆时，必须 (MUST) 根据记忆的流逝时间 $t$（基于 `last_active` 时间戳）和累计引用频次 `reference_count` 计算艾宾浩斯记忆保留度 $R$。
- **计算逻辑**：
  - 流逝天数 $t = (\text{当前时间} - last\_active) / 86400.0$。
  - 记忆强度 $S = S_{base} \times (1.0 + \ln(1.0 + reference\_count))$，其中基础半衰期 $S_{base}$ 从系统配置项中获取（默认 7.0）。
  - 保留度 $R = e^{-t / S}$。
- 系统必须 (MUST) 确保 $R$ 的取值范围在 $[0.0, 1.0]$ 之间。

#### Scenario: Active and frequently referenced memory retrievability
- **WHEN** 检索到一条在 1.5 天前活跃且引用过 10 次的记忆，基础半衰期为 7.0 天
- **THEN** 记忆强度 $S$ 将被对数增强，计算出的保留度 $R$ 必须在 $0.90$ 以上，记忆衰减极少

#### Scenario: Stale memory with zero reference retrievability
- **WHEN** 检索到一条在 30 天前活跃且引用次数为 0 的记忆，基础半衰期为 7.0 天
- **THEN** 计算出的保留度 $R$ 必须在 $0.05$ 以下，表明记忆发生严重遗忘衰减

### Requirement: LTM Retrieval Reranking
系统在从 Redis 向量检索（FT.SEARCH）中检索出粗筛的 $K$ 条长期记忆数据后，必须 (MUST) 将每条记忆的向量相关度得分 `score` 乘以其艾宾浩斯保留度 $R$，得到最终的综合评分，并根据该得分对所有召回数据进行降序重新排列。

#### Scenario: Stale high-match memory vs Fresh low-match memory
- **WHEN** 向量相似度粗筛出两条记忆：记忆 A 相似度 0.90（30天前活跃，0次引用），记忆 B 相似度 0.80（1天前活跃，3次引用）
- **THEN** 经过艾宾浩斯保留度乘积加权重排后，系统必须使记忆 B 的最终权重分值大于记忆 A，并在召回结果列表中优先返回记忆 B

