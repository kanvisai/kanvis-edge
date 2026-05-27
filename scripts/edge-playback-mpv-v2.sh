#!/usr/bin/env bash
# Playback RTSP v2 — usa exactamente el mismo formato de URL que el componente C1.
# Formato: starttime=2026-05-26T17%3A18%3A39Z&endtime=2026-05-26T17%3A19%3A12Z
#
# Uso:
#   ./scripts/edge-playback-mpv-v2.sh
#   CAMERA_PREFIX=cam-10-71-23-164-ch-101 START_OFFSET_SEC=10 ./scripts/edge-playback-mpv-v2.sh

set -euo pipefail

EDGE_HOST="${EDGE_HOST:-josafersl.ddns.net}"
EDGE_PORT="${EDGE_PORT:-8554}"
RTSP_USER="${RTSP_USER:-kanvis}"
RTSP_PASS="${RTSP_PASS:-123456789aA%40}"
CAMERA_PREFIX="${CAMERA_PREFIX:-cam-01}"
PLAYBACK_CHANNEL="${PLAYBACK_CHANNEL:-101}"
START_OFFSET_SEC="${START_OFFSET_SEC:-10}"
PLAYBACK_DURATION_SEC="${PLAYBACK_DURATION_SEC:-33}"

_fmt_local() {
  date -d "@$1" +"%Y-%m-%d %H:%M:%S %Z" 2>/dev/null || date -r "$1" +"%Y-%m-%d %H:%M:%S %Z"
}

now=$(date +%s)
start_epoch=$((now - START_OFFSET_SEC))
end_epoch=$((now + PLAYBACK_DURATION_SEC))

# Formato C1: ISO 8601 UTC con Z, URL-encoded (%3A en vez de :)
starttime_raw=$(date -u -d "@${start_epoch}" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
             || date -u -r "${start_epoch}" +"%Y-%m-%dT%H:%M:%SZ")
endtime_raw=$(date -u -d "@${end_epoch}" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
           || date -u -r "${end_epoch}" +"%Y-%m-%dT%H:%M:%SZ")

starttime=$(printf '%s' "$starttime_raw" | sed 's/:/%3A/g')
endtime=$(printf '%s' "$endtime_raw" | sed 's/:/%3A/g')

PATH_RTSP="${CAMERA_PREFIX}/Streaming/tracks/${PLAYBACK_CHANNEL}"
URL="rtsp://${RTSP_USER}:${RTSP_PASS}@${EDGE_HOST}:${EDGE_PORT}/${PATH_RTSP}?starttime=${starttime}&endtime=${endtime}"

echo "========================================="
echo " Kanvis Edge — playback v2 (formato C1)"
echo "========================================="
echo "Edge (gateway RTSP)             : ${EDGE_HOST}:${EDGE_PORT}"
echo "Cámara (prefix)                 : ${CAMERA_PREFIX}"
echo "Canal playback                  : ${PLAYBACK_CHANNEL}  (path: ${PATH_RTSP})"
echo "Hora local (ahora)              : $(_fmt_local "${now}")"
echo "Inicio ventana (-${START_OFFSET_SEC}s)          : $(_fmt_local "${start_epoch}")"
echo "Fin ventana (+${PLAYBACK_DURATION_SEC}s)           : $(_fmt_local "${end_epoch}")"
echo "-----------------------------------------"
echo "starttime (UTC, raw)            : ${starttime_raw}"
echo "endtime   (UTC, raw)            : ${endtime_raw}"
echo "starttime (URL-encoded)         : ${starttime}"
echo "endtime   (URL-encoded)         : ${endtime}"
echo "========================================="
echo "URL completa (idéntica a formato C1):"
echo "${URL}"
echo "========================================="
echo ""
echo "Lanzando MPV (RTSP-TCP, sin audio)..."

exec mpv --rtsp-transport=tcp --no-audio -- "${URL}"
