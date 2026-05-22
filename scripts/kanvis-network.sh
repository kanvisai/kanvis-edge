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
# shellcheck source=lib/install-access.sh
source "${SCRIPT_DIR}/lib/install-access.sh"

load_env_file_safe() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" ]] || continue
    [[ "$line" == *"="* ]] || continue
    key="${line%%=*}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    val="${line#*=}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    export "${key}=${val}"
  done < "$f"
}

# Solo /etc/kanvis-edge/env ( .env en /opt es enlace al mismo fichero )
load_env_file_safe "$ENV_FILE"

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

# Escapa sustitución en sed (delimitador |).
_sed_repl() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//|/\\|}"
  s="${s//&/\\&}"
  printf '%s' "$s"
}

render_template() {
  local template="$1" dest="$2"
  local iface ssid ch ip dstart dend
  iface="$(_sed_repl "$WLAN_INTERFACE")"
  ssid="$(_sed_repl "$AP_SSID")"
  ch="$(_sed_repl "$AP_CHANNEL")"
  ip="$(_sed_repl "$AP_IP")"
  dstart="$(_sed_repl "$DHCP_START")"
  dend="$(_sed_repl "$DHCP_END")"
  # @WPA_BLOCK@ no puede ir en sed: contiene varias líneas → rompe sed (char 19 unterminated `s').
  sed -e "s|@WLAN_INTERFACE@|${iface}|g" \
      -e "s|@AP_SSID@|${ssid}|g" \
      -e "s|@AP_CHANNEL@|${ch}|g" \
      -e "s|@AP_IP@|${ip}|g" \
      -e "s|@DHCP_START@|${dstart}|g" \
      -e "s|@DHCP_END@|${dend}|g" \
      -e '/^@WPA_BLOCK@$/d' \
      "$template" > "$dest"
  if [[ -n "${AP_PASSWORD}" ]]; then
    {
      echo "wpa=2"
      echo "wpa_key_mgmt=WPA-PSK"
      printf 'wpa_passphrase=%s\n' "$AP_PASSWORD"
      echo "rsn_pairwise=CCMP"
    } >> "$dest"
  fi
}

iface_exists() {
  ip link show "$1" &>/dev/null
}

# Evita "address already in use (port 53)" con systemd-resolved / dnsmasq del sistema.
prepare_dnsmasq_for_ap() {
  systemctl stop dnsmasq.service 2>/dev/null || true
  systemctl disable dnsmasq.service 2>/dev/null || true
  pkill -x dnsmasq 2>/dev/null || true
  sleep 0.5
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

_detect_wifi_interface() {
  if iface_exists "$WLAN_INTERFACE"; then
    return 0
  fi
  local cand
  for cand in wlP1p1s0 wlp1s0 wlan0 wlan1; do
    if iface_exists "$cand"; then
      log "AVISO: ${WLAN_INTERFACE} no existe; usando ${cand}"
      WLAN_INTERFACE="$cand"
      return 0
    fi
  done
  return 1
}

setup_ap() {
  if ! _detect_wifi_interface; then
    log "ERROR: interfaz WiFi no encontrada (configura WLAN_INTERFACE en ${ENV_FILE})"
    log "  Interfaces: $(ip -br link 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
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
    prepare_dnsmasq_for_ap
    pkill -f "dnsmasq.*${DNSMASQ_CONF}" 2>/dev/null || true
    if ! dnsmasq -C "$DNSMASQ_CONF" -x "${STATE_DIR}/dnsmasq.pid"; then
      log "ERROR: dnsmasq no arrancó (revisa journalctl -u kanvis-network). Si persiste puerto 53: systemctl status systemd-resolved"
      exit 1
    fi
    log "dnsmasq AP: solo DHCP (port=0, sin DNS en :53)"
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
