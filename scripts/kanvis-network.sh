#!/usr/bin/env bash
# Kanvis Edge — configuración de red (Fase 5)
# Uso: kanvis-network.sh start|stop|status
set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-/opt/kanvis-edge}"
ENV_FILE="${ENV_FILE:-/etc/kanvis-edge/env}"
STATE_DIR="/run/kanvis-edge"
HOSTAPD_CONF="${STATE_DIR}/hostapd.conf"
DNSMASQ_CONF="${STATE_DIR}/dnsmasq-ap.conf"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/distro.sh
source "${SCRIPT_DIR}/lib/distro.sh"

# shellcheck source=/dev/null
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
[[ -f "${INSTALL_ROOT}/.env" ]] && set -a && source "${INSTALL_ROOT}/.env" && set +a

NETWORK_MODE="${NETWORK_MODE:-ap_and_lan}"
WLAN_INTERFACE="${WLAN_INTERFACE:-wlan0}"
LAN_INTERFACE="${LAN_INTERFACE:-eth0}"
AP_SSID_PREFIX="${AP_SSID_PREFIX:-kanvis}"
AP_IP="${AP_IP:-192.168.192.192}"
AP_NETMASK="${AP_NETMASK:-24}"
AP_CHANNEL="${AP_CHANNEL:-6}"
AP_PASSWORD="${AP_PASSWORD:-kanvis-install}"
DEVICE_ID="${DEVICE_ID:-$(cat /etc/machine-id 2>/dev/null | head -c 6 || hostname | tr -cd 'a-zA-Z0-9' | head -c 6)}"
AP_SSID="${AP_SSID_PREFIX}-${DEVICE_ID}"
DHCP_START="192.168.192.50"
DHCP_END="192.168.192.150"

log() { echo "[kanvis-network] $*"; }

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    log "Ejecutar como root"
    exit 1
  fi
}

render_template() {
  local template="$1" dest="$2"
  local wpa_block=""
  if [[ -n "${AP_PASSWORD}" ]]; then
    wpa_block=$'wpa=2\nwpa_key_mgmt=WPA-PSK\nwpa_passphrase='"${AP_PASSWORD}"$'\nrsn_pairwise=CCMP'
  fi
  sed -e "s|@WLAN_INTERFACE@|${WLAN_INTERFACE}|g" \
      -e "s|@AP_SSID@|${AP_SSID}|g" \
      -e "s|@AP_CHANNEL@|${AP_CHANNEL}|g" \
      -e "s|@AP_IP@|${AP_IP}|g" \
      -e "s|@DHCP_START@|${DHCP_START}|g" \
      -e "s|@DHCP_END@|${DHCP_END}|g" \
      -e "s|@WPA_BLOCK@|${wpa_block}|g" \
      "$template" > "$dest"
}

iface_exists() {
  ip link show "$1" &>/dev/null
}

setup_lan() {
  if ! iface_exists "$LAN_INTERFACE"; then
    log "Interfaz LAN ${LAN_INTERFACE} no encontrada — omitiendo"
    return 0
  fi
  log "LAN ${LAN_INTERFACE}: DHCP cliente (conectar a router de tienda)"
  ip link set "$LAN_INTERFACE" up 2>/dev/null || true
  if command -v dhclient &>/dev/null; then
    dhclient -v "$LAN_INTERFACE" 2>/dev/null || dhclient "$LAN_INTERFACE" || true
  elif command -v dhcpcd &>/dev/null; then
    dhcpcd "$LAN_INTERFACE" || true
  fi
}

setup_ap() {
  if ! iface_exists "$WLAN_INTERFACE"; then
    log "ERROR: interfaz WiFi ${WLAN_INTERFACE} no existe"
    exit 1
  fi
  mkdir -p "$STATE_DIR"

  local distro
  distro="$(detect_kanvis_distro)"
  log "Distro detectada: ${distro}"
  prepare_kanvis_ap_network "$WLAN_INTERFACE" "$INSTALL_ROOT"

  render_template "${INSTALL_ROOT}/deploy/network/hostapd.conf.template" "$HOSTAPD_CONF"
  render_template "${INSTALL_ROOT}/deploy/network/dnsmasq-ap.conf.template" "$DNSMASQ_CONF"

  log "AP SSID=${AP_SSID} IP=${AP_IP}/${AP_NETMASK} en ${WLAN_INTERFACE}"

  ip addr flush dev "$WLAN_INTERFACE" 2>/dev/null || true
  ip link set "$WLAN_INTERFACE" up
  ip addr add "${AP_IP}/${AP_NETMASK}" dev "$WLAN_INTERFACE"

  sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true

  if command -v hostapd &>/dev/null; then
    pkill -f "hostapd.*${HOSTAPD_CONF}" 2>/dev/null || true
    hostapd -B "$HOSTAPD_CONF"
  else
    log "ERROR: instala hostapd (apt install hostapd)"
    exit 1
  fi

  if command -v dnsmasq &>/dev/null; then
    pkill -f "dnsmasq.*${DNSMASQ_CONF}" 2>/dev/null || true
    dnsmasq -C "$DNSMASQ_CONF" -x "${STATE_DIR}/dnsmasq.pid"
  else
    log "AVISO: dnsmasq no instalado — clientes WiFi sin DHCP"
  fi
}

stop_ap() {
  pkill -f "hostapd.*${HOSTAPD_CONF}" 2>/dev/null || true
  pkill -f "dnsmasq.*${DNSMASQ_CONF}" 2>/dev/null || true
  if iface_exists "$WLAN_INTERFACE"; then
    ip addr flush dev "$WLAN_INTERFACE" 2>/dev/null || true
  fi
  restore_kanvis_ap_network "$WLAN_INTERFACE"
}

cmd_start() {
  need_root
  case "$NETWORK_MODE" in
    ap_only)
      setup_ap
      ;;
    lan_only)
      setup_lan
      ;;
    ap_and_lan|*)
      setup_ap
      setup_lan
      ;;
  esac
  echo "$AP_SSID" > "${STATE_DIR}/ap-ssid" 2>/dev/null || true
  log "Listo. Panel: http://${AP_IP}:8000/"
}

cmd_stop() {
  need_root
  stop_ap
  log "AP detenido"
}

cmd_status() {
  echo "DISTRO=$(detect_kanvis_distro)"
  echo "NETWORK_MODE=${NETWORK_MODE}"
  echo "AP_SSID=${AP_SSID}"
  echo "AP_IP=${AP_IP}"
  echo "WLAN=${WLAN_INTERFACE} LAN=${LAN_INTERFACE}"
  ip -br addr show "$WLAN_INTERFACE" 2>/dev/null || true
  ip -br addr show "$LAN_INTERFACE" 2>/dev/null || true
  pgrep -a hostapd 2>/dev/null || echo "hostapd: no"
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *)
    echo "Uso: $0 {start|stop|status}"
    exit 1
    ;;
esac
