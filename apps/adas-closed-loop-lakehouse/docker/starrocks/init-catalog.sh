#!/usr/bin/env bash
# =============================================================================
# StarRocks 初始化：External Catalog（直查 Paimon）+ 内表库。
#
# 名字逐字取自 src/adas_lakehouse/config.py：
#   StarRocksConfig.external_catalog   默认 paimon_catalog
#   StarRocksConfig.internal_database  默认 adas_ads
#   MinioConfig.warehouse_path         s3://adas-lakehouse/warehouse
#
# ⚠️ 与 ddl/starrocks_*.sql 的差别只有一个字段：endpoint。
#    仓库里的 SQL 写的是 http://localhost:18600（宿主机发布端口，面向宿主机侧客户端），
#    但 StarRocks 跑在容器里，localhost 是它自己，必须换成服务名 minio。
#    所以本脚本用 ${MINIO_ENDPOINT}（compose 里给的是 http://minio:9000），
#    建完之后 StarRocks 这一路就能直接读湖，不用你手工改任何 SQL。
#
# 这只建 catalog 与库；ddl/starrocks_*.sql 里的视图与内表请在栈起来后自己执行：
#   mysql -h 127.0.0.1 -P 18630 -u root < ddl/starrocks_ingest.sql   # 宿主机 18630 → 容器 9030
# =============================================================================
set -euo pipefail

FE_HOST="${STARROCKS_FE_HOST:-starrocks}"
FE_PORT="${STARROCKS_QUERY_PORT:-9030}"
SR_USER="${STARROCKS_USER:-root}"
SR_PASSWORD="${STARROCKS_PASSWORD:-}"
CATALOG="${STARROCKS_EXTERNAL_CATALOG:-paimon_catalog}"
DATABASE="${STARROCKS_DATABASE:-adas_ads}"
WAREHOUSE="s3://${PAIMON_WAREHOUSE_BUCKET:-adas-lakehouse}/warehouse"
S3_ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"

mysql_args=(-h "${FE_HOST}" -P "${FE_PORT}" -u "${SR_USER}" --protocol=TCP)
if [ -n "${SR_PASSWORD}" ]; then
  mysql_args+=("-p${SR_PASSWORD}")
fi

echo "[starrocks-init] 等待 FE 可用 ${FE_HOST}:${FE_PORT} ..."
for i in $(seq 1 60); do
  if mysql "${mysql_args[@]}" -e "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  sleep 5
  if [ "${i}" -eq 60 ]; then
    echo "[starrocks-init] FE 始终连不上，放弃" >&2
    exit 1
  fi
done

echo "[starrocks-init] 建 External Catalog ${CATALOG} -> ${WAREHOUSE} @ ${S3_ENDPOINT}"
mysql "${mysql_args[@]}" <<SQL
CREATE EXTERNAL CATALOG IF NOT EXISTS \`${CATALOG}\`
PROPERTIES (
  'type' = 'paimon',
  'paimon.catalog.type' = 'filesystem',
  'paimon.catalog.warehouse' = '${WAREHOUSE}',
  'aws.s3.endpoint' = '${S3_ENDPOINT}',
  'aws.s3.access_key' = '${MINIO_ACCESS_KEY}',
  'aws.s3.secret_key' = '${MINIO_SECRET_KEY}',
  'aws.s3.enable_path_style_access' = 'true'
);

CREATE DATABASE IF NOT EXISTS \`${DATABASE}\`;
SQL

echo "[starrocks-init] 现有 catalog："
mysql "${mysql_args[@]}" -e "SHOW CATALOGS"
echo "[starrocks-init] done"
