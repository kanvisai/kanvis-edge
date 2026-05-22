#!/usr/bin/env bash
# Detección de distro y preparación de red para AP (Raspberry Pi OS vs Debian/genérico)
# shellcheck disable=SC2034

KANVIS_DISTRO_UNKNOWN="unknown"
KANVIS_DISTRO_DEBIAN="debian"
KANVIS_DISTRO_RASPIOS="raspberry_pi_os"

detect_kanvis_distro() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    local ID ID_LIKE NAME
    # shellcheck source=/dev/null
    . /etc/os-release
    ID="${ID:-}"
    ID_LIKE="${ID_LIKE:-}"
    NAME="${NAME:-}"
    case "${ID} ${ID_LIKE} ${NAME}" in
      *raspbian*|*raspberry*|*Raspberry*)
        echo "$KANVIS_DISTRO_RASPIOS"
        return 0
        ;;
      *debian*|*ubuntu*)
        echo "$KANVIS_DISTRO_DEBIAN"
        return 0
        ;;
    esac
  fi
  if [[ -f /usr/bin/raspi-config ]] || [[ -d /usr/lib/raspberrypi ]]; then
    echo "$KANVIS_DISTRO_RASPIOS"
    return 0
  fi
  if [[ -f /etc/debian_version ]]; then
    echo "$KANVIS_DISTRO_DEBIAN"
    return 0
  fi
  echo "$KANVIS_DISTRO_UNKNOWN"
}

# Libera wlan0 de NetworkManager / dhcpcd antes de hostapd
_prepare_wlan_generic() {
  local iface="$1"
  rfkill unblock wifi 2>/dev/null || true
  ip link set "$iface" down 2>/dev/null || true

  if command -v nmcli &>/dev/null && systemctl is-active NetworkManager &>/dev/null; then
    nmcli dev disconnect "$iface" 2>/dev/null || true
    nmcli dev set "$iface" managed no 2>/dev/null || true
  fi
}

_prepare_raspberry_pi_os() {
  local iface="$1"
  local install_root="$2"
  log_distro "Raspberry Pi OS: preparando AP en ${iface}"

  # Paquetes en RPi OS suelen traer hostapd/dnsmasq enmascarados
  systemctl unmask hostapd dnsmasq 2>/dev/null || true
  systemctl stop hostapd dnsmasq 2>/dev/null || true
  systemctl disable hostapd dnsmasq 2>/dev/null || true

  # No usar el hostapd del sistema; Kanvis lanza el suyo con conf en /run
  if [[ -f /etc/default/hostapd ]]; then
    if ! grep -q "KANVIS_MANAGED" /etc/default/hostapd 2>/dev/null; then
      cat >> /etc/default/hostapd <<'EOF'

# KANVIS_MANAGED — hostapd lo arranca kanvis-network.sh
DAEMON_CONF=""
EOF
    fi
  fi

  # dhcpcd no debe gestionar la interfaz AP
  if [[ -f /etc/dhcpcd.conf ]] && ! grep -q "denyinterfaces ${iface}" /etc/dhcpcd.conf; then
    echo "" >> /etc/dhcpcd.conf
    echo "# Kanvis Edge AP" >> /etc/dhcpcd.conf
    echo "denyinterfaces ${iface}" >> /etc/dhcpcd.conf
    systemctl restart dhcpcd 2>/dev/null || true
  fi

  # wpa_supplicant en wlan0 impide hostapd en Bookworm
  systemctl stop wpa_supplicant 2>/dev/null || true
  systemctl stop "wpa_supplicant@${iface}.service" 2>/dev/null || true
  if command -v systemctl &>/dev/null; then
    systemctl mask "wpa_supplicant@${iface}.service" 2>/dev/null || true
  fi

  _prepare_wlan_generic "$iface"

  # País WiFi (requerido en algunas revisiones de RPi OS)
  if command -v raspi-config &>/dev/null; then
    raspi-config nonint do_wifi_country ES 2>/dev/null || true
  fi
}

_prepare_debian_generic() {
  local iface="$1"
  log_distro "Debian/genérico: preparando AP en ${iface}"

  systemctl stop hostapd dnsmasq 2>/dev/null || true
  systemctl disable hostapd dnsmasq 2>/dev/null || true

  _prepare_wlan_generic "$iface"
}

prepare_kanvis_ap_network() {
  local iface="${1:-wlan0}"
  local install_root="${2:-/opt/kanvis-edge}"
  local distro
  distro="$(detect_kanvis_distro)"
  echo "$distro" > /run/kanvis-edge/distro 2>/dev/null || mkdir -p /run/kanvis-edge && echo "$distro" > /run/kanvis-edge/distro

  case "$distro" in
    "$KANVIS_DISTRO_RASPIOS")
      _prepare_raspberry_pi_os "$iface" "$install_root"
      ;;
    "$KANVIS_DISTRO_DEBIAN"|*)
      _prepare_debian_generic "$iface" "$install_root"
      ;;
  esac
}

log_distro() {
  echo "[kanvis-distro] $*"
}

restore_kanvis_ap_network() {
  local iface="${1:-wlan0}"
  local distro
  distro="$(detect_kanvis_distro)"

  if command -v nmcli &>/dev/null; then
    nmcli dev set "$iface" managed yes 2>/dev/null || true
  fi

  if [[ "$distro" == "$KANVIS_DISTRO_RASPIOS" ]]; then
    systemctl unmask "wpa_supplicant@${iface}.service" 2>/dev/null || true
  fi
}
