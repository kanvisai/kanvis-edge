#!/usr/bin/env bash
# Vivo RTSP rebroadcast (FFmpeg relay) — distinto del playback con búfer.
# Ruta típica: /cam-<host>-ch-<canal> (la muestra el panel en «Broadcast RTSP»).
#
# Uso:
#   RELAY_PATH=cam-192-168-1-68-ch-stream1 ./scripts/edge-relay-live-mpv.sh

set -euo pipefail

EDGE_HOST="${EDGE_HOST:-josafersl.ddns.net}"
EDGE_PORT="${EDGE_PORT:-8554}"
RTSP_USER="${RTSP_USER:-kanvis}"
RTSP_PASS="${RTSP_PASS:-123456789aA%40}"
RELAY_PATH="${RELAY_PATH:-cam-192-168-1-68-ch-stream1}"

auth=""
if [[ -n "${RTSP_USER}" ]]; then
  if [[ -n "${RTSP_PASS}" ]]; then
    auth="${RTSP_USER}:${RTSP_PASS}@"
  else
    auth="${RTSP_USER}@"
  fi
fi

URL="rtsp://${auth}${EDGE_HOST}:${EDGE_PORT}/${RELAY_PATH}"

echo "Relay vivo: ${URL}"
echo "Requiere broadcast RTSP activo y relay escuchando (ss -tlnp | grep ${EDGE_PORT})"
exec mpv --rtsp-transport=tcp --no-audio "${URL}"
