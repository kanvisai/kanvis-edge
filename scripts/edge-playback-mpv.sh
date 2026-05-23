#!/usr/bin/env bash
# Playback RTSP con búfer Kanvis Edge (MediaMTX + starttime/endtime).
# No usar la IP de la cámara: el edge sirve el tramo reciente desde RAM.
#
# Requisitos en el guardia:
#   RTSP_GATEWAY_ENABLED=true en /etc/kanvis-edge/env
#   Cámara con output.gateway.enabled=true, brand tplink, channel=stream1
#   Broadcast activo (búfer llenándose) o ingesta por gateway
#   POST /api/v1/gateway/reload tras cambiar config
#
# Uso (desde cualquier PC en la LAN; no hace falta estar en el guardia):
#   EDGE_HOST=192.168.1.100 ./scripts/edge-playback-mpv.sh
#
# Desde internet (misma URL, IP/puerto públicos + PF en el router):
#   EDGE_HOST=176.85.146.32 EDGE_PORT=55422 ./scripts/edge-playback-mpv.sh
#
# PLAYBACK_CHANNEL: mismo id que en el panel (TP-Link suele ser stream1).
# PLAYBACK_TIME=local (defecto, TP-Link) | utc (Annke/Hik y similares)

set -euo pipefail

EDGE_HOST="${EDGE_HOST:-192.168.1.100}"
EDGE_PORT="${EDGE_PORT:-8554}" 
RTSP_USER="${RTSP_USER:-camera}"
RTSP_PASS="${RTSP_PASS:-camera69}"
PLAYBACK_CHANNEL="${PLAYBACK_CHANNEL:-stream1}"
START_OFFSET_SEC="${START_OFFSET_SEC:-6}"
PLAYBACK_DURATION_SEC="${PLAYBACK_DURATION_SEC:-30}"
PLAYBACK_TIME="${PLAYBACK_TIME:-local}"

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

PATH_RTSP="Streaming/tracks/${PLAYBACK_CHANNEL}"
URL="rtsp://${RTSP_USER}:${RTSP_PASS}@${EDGE_HOST}:${EDGE_PORT}/${PATH_RTSP}?starttime=${starttime}&endtime=${endtime}"

echo "========================================="
echo " Kanvis Edge — playback (búfer + vivo)"
echo "========================================="
echo "Edge (gateway RTSP)             : ${EDGE_HOST}:${EDGE_PORT}"
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

exec mpv --rtsp-transport=tcp --no-audio "${URL}"
