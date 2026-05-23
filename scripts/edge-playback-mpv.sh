#!/usr/bin/env bash
# Playback RTSP con búfer Kanvis Edge (MediaMTX + starttime/endtime).
# No usar la IP de la cámara: el edge sirve el tramo reciente desde RAM.
#
# Requisitos en el guardia:
#   RTSP_GATEWAY_ENABLED=true en /etc/kanvis-edge/env
#   Cámara con output.gateway.enabled=true, brand tplink, playback_channel=101
#   Broadcast activo (búfer llenándose) o ingesta por gateway
#   POST /api/v1/gateway/reload tras cambiar config
#
# Uso:
#   ./scripts/edge-playback-mpv.sh
#   EDGE_HOST=192.168.1.100 PLAYBACK_CHANNEL=101 ./scripts/edge-playback-mpv.sh

set -euo pipefail

EDGE_HOST="${EDGE_HOST:-192.168.1.100}"
EDGE_PORT="${EDGE_PORT:-8554}"
RTSP_USER="${RTSP_USER:-camera}"
RTSP_PASS="${RTSP_PASS:-camera69}"
# Canal en la URL inventada /Streaming/tracks/<id> (cameras.json → playback_channel)
PLAYBACK_CHANNEL="${PLAYBACK_CHANNEL:-101}"
# Ventana: últimos N s desde el búfer + M s hacia delante (vivo en el edge)
START_OFFSET_SEC="${START_OFFSET_SEC:-6}"
PLAYBACK_DURATION_SEC="${PLAYBACK_DURATION_SEC:-30}"

now=$(date +%s)
start_epoch=$((now - START_OFFSET_SEC))
end_epoch=$((now + PLAYBACK_DURATION_SEC))
starttime=$(date -u -d "@${start_epoch}" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -r "${start_epoch}" +"%Y-%m-%dT%H:%M:%SZ")
endtime=$(date -u -d "@${end_epoch}" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -r "${end_epoch}" +"%Y-%m-%dT%H:%M:%SZ")

PATH_RTSP="Streaming/tracks/${PLAYBACK_CHANNEL}"
URL="rtsp://${RTSP_USER}:${RTSP_PASS}@${EDGE_HOST}:${EDGE_PORT}/${PATH_RTSP}?starttime=${starttime}&endtime=${endtime}"

echo "========================================="
echo " Kanvis Edge — playback (búfer + vivo)"
echo "========================================="
echo "Edge (gateway RTSP)             : ${EDGE_HOST}:${EDGE_PORT}"
echo "Hora de lanzamiento             : $(date -d "@${now}" +"%H:%M:%S" 2>/dev/null || date -r "${now}" +"%H:%M:%S")"
echo "Inicio playback (-${START_OFFSET_SEC}s)     : $(date -d "@${start_epoch}" +"%H:%M:%S" 2>/dev/null || date -r "${start_epoch}" +"%H:%M:%S")"
echo "Fin playback (+${PLAYBACK_DURATION_SEC}s)   : $(date -d "@${end_epoch}" +"%H:%M:%S" 2>/dev/null || date -r "${end_epoch}" +"%H:%M:%S")"
echo "========================================="
echo "URL:"
echo "${URL}"
echo "========================================="
echo ""
echo "Comprobación rápida (en el edge): ss -tlnp | grep ${EDGE_PORT}"
echo "Si connection refused: activa RTSP_GATEWAY_ENABLED y gateway.reload"
echo ""
echo "Lanzando MPV (RTSP-TCP, sin audio)..."

exec mpv --rtsp-transport=tcp --no-audio "${URL}"
