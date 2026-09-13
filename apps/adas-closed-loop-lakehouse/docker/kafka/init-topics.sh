#!/usr/bin/env bash
# =============================================================================
# Kafka 初始化：建 topic。
#
# topic 名逐字取自代码：
#   vehicle.trigger.event   config.KafkaConfig.trigger_topic      车端回传触发事件
#   production.event        config.KafkaConfig.production_topic   产线事件
#   collect.file.meta       ingest/channels.py KAFKA_FILE_META_TOPIC  通道三文件元信息
#   lineage.change.event    flink/sql/lineage_realtime_sync.sql      血缘变更事件
#
# 三分区 / 单副本：本地单 broker，副本数只能是 1；分区给 3 是为了让
# FLINK_PARALLELISM=2 及以上时 source 有得并行。
# =============================================================================
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP:-kafka:29092}"
PARTITIONS="${KAFKA_INIT_PARTITIONS:-3}"
REPLICATION="${KAFKA_INIT_REPLICATION:-1}"

TOPICS=(
  "${KAFKA_TRIGGER_TOPIC:-vehicle.trigger.event}"
  "${KAFKA_PRODUCTION_TOPIC:-production.event}"
  "${KAFKA_FILE_META_TOPIC:-collect.file.meta}"
  "${KAFKA_LINEAGE_TOPIC:-lineage.change.event}"
)

for topic in "${TOPICS[@]}"; do
  echo "[kafka-init] create-if-not-exists ${topic}"
  /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server "${BOOTSTRAP}" \
    --create --if-not-exists \
    --topic "${topic}" \
    --partitions "${PARTITIONS}" \
    --replication-factor "${REPLICATION}"
done

echo "[kafka-init] 现有 topic："
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "${BOOTSTRAP}" --list
echo "[kafka-init] done"
