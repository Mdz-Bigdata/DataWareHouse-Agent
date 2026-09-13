// ============================================================================
// Neo4j 血缘图库初始化：五类节点各一个 id 唯一约束。
//
// 语句**原样**来自 src/adas_lakehouse/lineage/graph.py 的 CONSTRAINT_STATEMENTS，
// 本文件只是它的落盘副本，不是第二份定义。重新生成：
//   PYTHONPATH=src python3 -c "from adas_lakehouse.lineage.graph import \
//     CONSTRAINT_STATEMENTS as C; print(';\n'.join(C) + ';')"
//
// 为什么必须建：图库全量用 MERGE 幂等写入，而 Neo4j 的 MERGE 在没有唯一约束时
// 并发下会产生重复节点 —— 唯一约束是「MERGE 幂等」这条前提成立的必要条件。
//
// 由 neo4j-init 容器在 neo4j healthy 之后执行；IF NOT EXISTS，重复跑无副作用。
// ============================================================================

CREATE CONSTRAINT `lineage_clip_id` IF NOT EXISTS FOR (n:`Clip`) REQUIRE n.`id` IS UNIQUE;
CREATE CONSTRAINT `lineage_artifact_id` IF NOT EXISTS FOR (n:`Artifact`) REQUIRE n.`id` IS UNIQUE;
CREATE CONSTRAINT `lineage_run_id` IF NOT EXISTS FOR (n:`Run`) REQUIRE n.`id` IS UNIQUE;
CREATE CONSTRAINT `lineage_datasetversion_id` IF NOT EXISTS FOR (n:`DatasetVersion`) REQUIRE n.`id` IS UNIQUE;
CREATE CONSTRAINT `lineage_badcase_id` IF NOT EXISTS FOR (n:`Badcase`) REQUIRE n.`id` IS UNIQUE;
