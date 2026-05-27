#!/usr/bin/env bash
# Playback RTSP con búfer Kanvis Edge (MediaMTX + starttime/endtime).
# No usar la IP de la cámara: el edge sirve el tramo reciente desde RAM.
#
# Requisitos en el guardia:
#   RTSP_GATEWAY_ENABLED=true en /etc/kanvis-edge/env
#   Cámara con output.gateway.enabled=true, broadcast activo
#   POST /api/v1/gateway/reload tras cambiar config
#
# Uso (desde cualquier PC en la LAN; no hace falta estar en el guardia):
#   EDGE_HOST=192.168.1.100 CAMERA_PREFIX=cam-01 ./scripts/edge-playback-mpv.sh
#
# Desde internet (misma URL, IP/puerto públicos + PF en el router):
#   EDGE_HOST=josafersl.ddns.net EDGE_PORT=8554 CAMERA_PREFIX=cam-02 ./scripts/edge-playback-mpv.sh
#
# CAMERA_PREFIX: camera_id en el edge (cam-01, cam-02, etc.)
# PLAYBACK_CHANNEL: canal RTSP del fabricante (101 para Annke/Hik, stream1 para TP-Link)
# PLAYBACK_TIME=utc (defecto, Annke/Hik) | local (TP-Link)

set -euo pipefail

EDGE_HOST="${EDGE_HOST:-josafersl.ddns.net}"
EDGE_PORT="${EDGE_PORT:-8554}"
RTSP_USER="${RTSP_USER:-kanvis}"
RTSP_PASS="${RTSP_PASS:-123456789aA%40}"
CAMERA_PREFIX="${CAMERA_PREFIX:-cam-01}"
PLAYBACK_CHANNEL="${PLAYBACK_CHANNEL:-101}"
START_OFFSET_SEC="${START_OFFSET_SEC:-10}"
PLAYBACK_DURATION_SEC="${PLAYBACK_DURATION_SEC:-30}"
PLAYBACK_TIME="${PLAYBACK_TIME:-utc}"

_fmt_local() {
  date -d "@$1" +"%Y-%m-%d %H:%M:%S %Z" 2>/dev/null || date -r "$1" +"%Y-%m-%d %H:%M:%S %Z"
}

_fmt_utc_param() {
  date -u -d "@$1" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -r "$1" +"%Y-%m-%dT%H:%M:%SZ"
}

_fmt_local_param() {
  date -d "@$1" +"%Y-%m-%dT%H:%M:%S" 2>/dev/null || date -r "$1" +"%Y-%m-%dT%H:%M:%S"
}

_urlencode_time() {
  _fmt_local_param "$1" | sed 's/:/%3A/g'
}

now=$(date +%s)
start_epoch=$((now - START_OFFSET_SEC))
end_epoch=$((now + PLAYBACK_DURATION_SEC))

if [[ "${PLAYBACK_TIME}" == "utc" ]]; then
  starttime=$(_fmt_utc_param "${start_epoch}")
  endtime=$(_fmt_utc_param "${end_epoch}")
  starttime=$(printf '%s' "$starttime" | sed 's/:/%3A/g')
  endtime=$(printf '%s' "$endtime" | sed 's/:/%3A/g')
  time_note="UTC (Z), codificado para RTSP"
else
  starttime=$(_urlencode_time "${start_epoch}")
  endtime=$(_urlencode_time "${end_epoch}")
  time_note="hora local (sin Z), codificado para RTSP"
fi

PATH_RTSP="${CAMERA_PREFIX}/Streaming/tracks/${PLAYBACK_CHANNEL}"
URL="rtsp://${RTSP_USER}:${RTSP_PASS}@${EDGE_HOST}:${EDGE_PORT}/${PATH_RTSP}?starttime=${starttime}&endtime=${endtime}"

echo "========================================="
echo " Kanvis Edge — playback (búfer + vivo)"
echo "========================================="
echo "Edge (gateway RTSP)             : ${EDGE_HOST}:${EDGE_PORT}"
echo "Cámara (prefix)                 : ${CAMERA_PREFIX}"
echo "Canal playback                  : ${PLAYBACK_CHANNEL}  (path: ${PATH_RTSP})"
echo "Hora local (ahora)              : $(_fmt_local "${now}")"
echo "Inicio ventana (-${START_OFFSET_SEC}s)      : $(_fmt_local "${start_epoch}")"
echo "Fin ventana (+${PLAYBACK_DURATION_SEC}s)    : $(_fmt_local "${end_epoch}")"
echo "Parámetros URL (${time_note})"
echo "  starttime=${starttime}"
echo "  endtime=${endtime}"
echo "========================================="
echo "URL:"
echo "${URL}"
echo "========================================="
echo ""
echo "Comprobación rápida: nc -zv ${EDGE_HOST} ${EDGE_PORT}"
echo "En el edge: gateway/status → running:true y broadcast activo"
echo ""
echo "Lanzando MPV (RTSP-TCP, sin audio)..."

exec mpv --rtsp-transport=tcp --no-audio -- "${URL}"
