#!/usr/bin/env bash
# =============================================================================
# Flink SQL Gateway 入口
#
# 为什么不用官方 docker-entrypoint.sh：它只对 jobmanager / taskmanager /
# history-server / standalone-job 这几个已知命令处理 FLINK_PROPERTIES，
# 传 sql-gateway.sh 会被原样 exec，FLINK_PROPERTIES 一个字都不生效。
#
# 这里补上那一步。注意不能简单 `>>` 追加：Flink 1.20 用 SnakeYAML 解析
# config.yaml，同一个扁平 key 出现两次会直接抛
# `found duplicate key ...` 然后启动失败。所以先删同名 key 再追加
# ——语义与官方 entrypoint 的 set_config_option 一致。
#
# 口令走文件不走命令行参数，`ps` 里看不到 s3.secret-key。
# =============================================================================
set -euo pipefail

CONF_FILE=/opt/flink/conf/config.yaml
[ -f "${CONF_FILE}" ] || CONF_FILE=/opt/flink/conf/flink-conf.yaml

if [ -n "${FLINK_PROPERTIES:-}" ]; then
  echo "[sql-gateway] 合并 FLINK_PROPERTIES 到 ${CONF_FILE}"
  tmp="$(mktemp)"
  cp "${CONF_FILE}" "${tmp}"

  while IFS= read -r line; do
    # 去掉首尾空白；跳过空行与注释
    trimmed="$(printf '%s' "${line}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "${trimmed}" ] && continue
    case "${trimmed}" in \#*) continue ;; esac
    [[ "${trimmed}" != *:* ]] && continue

    key="${trimmed%%:*}"
    # key 里的正则元字符（主要是 `.`）要转义，否则会误删别的行
    key_re="$(printf '%s' "${key}" | sed 's/[].[^$*\/\\]/\\&/g')"
    sed -i "/^${key_re}[[:space:]]*:/d" "${tmp}"
  done <<< "${FLINK_PROPERTIES}"

  printf '\n%s\n' "${FLINK_PROPERTIES}" >> "${tmp}"
  cat "${tmp}" > "${CONF_FILE}"
  rm -f "${tmp}"
fi

# 端口/监听地址在 conf 里已经设过（docker/flink/conf/config.yaml），
# 这里再显式传一次，保证即使有人清空了 conf 也能起来。
exec /opt/flink/bin/sql-gateway.sh start-foreground \
  -Dsql-gateway.endpoint.type=rest \
  -Dsql-gateway.endpoint.rest.address=0.0.0.0 \
  -Dsql-gateway.endpoint.rest.port=8083 \
  "$@"
