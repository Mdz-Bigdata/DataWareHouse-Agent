#!/bin/sh
# =============================================================================
# MinIO 初始化：建桶。
#
# 桶名不是随便起的，逐字取自 src/adas_lakehouse/config.py：
#   MinioConfig.warehouse_bucket  默认 adas-lakehouse → s3://adas-lakehouse/warehouse
#   MinioConfig.raw_bucket        默认 adas-raw       → 合规入湖的原始文件
#
# 幂等：--ignore-existing，重复 up 不会报错。
# =============================================================================
set -eu

ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"
WAREHOUSE_BUCKET="${PAIMON_WAREHOUSE_BUCKET:-adas-lakehouse}"
RAW="${RAW_BUCKET:-adas-raw}"

echo "[minio-init] alias -> ${ENDPOINT}"
mc alias set adas "${ENDPOINT}" "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}"

for bucket in "${WAREHOUSE_BUCKET}" "${RAW}"; do
  echo "[minio-init] 建桶 ${bucket}"
  mc mb --ignore-existing "adas/${bucket}"
done

# warehouse 前缀由 Paimon 自己创建，这里只放一个占位对象，
# 方便 `mc ls` 一眼看出这个桶归谁用（Paimon 不会被它干扰）。
printf '%s\n' "Paimon warehouse root for adas-closed-loop-lakehouse." \
  | mc pipe "adas/${WAREHOUSE_BUCKET}/warehouse/.adas-keep"

echo "[minio-init] 现有桶："
mc ls adas
echo "[minio-init] done"
