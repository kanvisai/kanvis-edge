#!/usr/bin/env bash
# Vivo RTSP gateway (misma ruta que «Streaming/channels» en gateway/status).
# No confundir con playback (Streaming/tracks + starttime/endtime).
#
# Uso:
#   ./scripts/edge-relay-live-mpv.sh
#   CAMERA_PREFIX=cam-10-71-23-164-ch-101 ./scripts/edge-relay-live-mpv.sh
#   RELAY_PATH=cam-01/Streaming/channels/101 ./scripts/edge-relay-live-mpv.sh

set -euo pipefail

EDGE_HOST="${EDGE_HOST:-josafersl.ddns.net}"
EDGE_PORT="${EDGE_PORT:-8554}"
RTSP_USER="${RTSP_USER:-kanvis}"
RTSP_PASS="${RTSP_PASS:-123456789aA%40}"
CAMERA_PREFIX="${CAMERA_PREFIX:-cam-01}"
LIVE_CHANNEL="${LIVE_CHANNEL:-101}"
RELAY_PATH="${RELAY_PATH:-${CAMERA_PREFIX}/Streaming/channels/${LIVE_CHANNEL}}"

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
echo "Puerto por defecto ${EDGE_PORT} (si falla, prueba EDGE_PORT=55422 según gateway/status)"
echo "Requiere broadcast activo y gateway running (curl gateway/status)"
exec mpv --rtsp-transport=tcp --no-audio -- "${URL}"
