#!/usr/bin/env bash
# Comprobación e instalación de dependencias APT para Kanvis Edge.
set -euo pipefail

# name|paquete_apt|comando_opcional|grupo
PREFLIGHT_DEPS=(
  "Python 3|python3|python3|core"
  "Python venv|python3-venv|python3|core"
  "Python pip|python3-pip|pip3|core"
  "FFmpeg|ffmpeg|ffmpeg|core"
  "curl|curl|curl|core"
  "rsync|rsync|rsync|core"
  "OpenSSL|openssl|openssl|core"
  "iptables|iptables|iptables|core"
  "OpenSSH servidor|openssh-server|sshd|acceso"
  "xauth (SSH -X)|xauth|xauth|acceso"
  "dbus-x11 (SSH -X)|dbus-x11||acceso"
  "TigerVNC|tigervnc-standalone-server|vncserver|acceso"
  "hostapd (WiFi AP)|hostapd|hostapd|red_ap"
  "dnsmasq (DHCP AP)|dnsmasq|dnsmasq|red_ap"
)

preflight_pkg_installed() {
  local pkg="$1"
  dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"
}

preflight_cmd_exists() {
  command -v "$1" &>/dev/null
}

preflight_check_one() {
  local label="$1" pkg="$2" cmd="${3:-}"
  if preflight_pkg_installed "$pkg"; then
    if [[ -n "$cmd" ]] && ! preflight_cmd_exists "$cmd"; then
      ui_check_line "$label" "fail"
      return 1
    fi
    ui_check_line "$label" "ok"
    return 0
  fi
  ui_check_line "$label" "fail"
  return 1
}

preflight_scan_missing() {
  PREFLIGHT_MISSING=()
  local entry label pkg cmd
  for entry in "${PREFLIGHT_DEPS[@]}"; do
    IFS='|' read -r label pkg cmd _ <<< "$entry"
    if ! preflight_pkg_installed "$pkg"; then
      PREFLIGHT_MISSING+=("$pkg")
    elif [[ -n "$cmd" ]] && ! preflight_cmd_exists "$cmd"; then
      PREFLIGHT_MISSING+=("$pkg")
    fi
  done
}

preflight_install_missing() {
  preflight_scan_missing
  if [[ ${#PREFLIGHT_MISSING[@]} -eq 0 ]]; then
    ui_ok "Todas las dependencias APT ya están instaladas"
    return 0
  fi
  ui_section "Instalando paquetes faltantes"
  ui_detail "Paquetes: ${PREFLIGHT_MISSING[*]}"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${PREFLIGHT_MISSING[@]}"
  ui_ok "Instalación APT completada"
}

preflight_show_all_checks() {
  local entry label pkg cmd group
  local had_fail=0
  ui_section "Dependencias del sistema"
  for entry in "${PREFLIGHT_DEPS[@]}"; do
    IFS='|' read -r label pkg cmd group <<< "$entry"
    preflight_check_one "$label" "$pkg" "$cmd" || had_fail=1
  done
  if command -v raspi-config &>/dev/null; then
    ui_detail "Raspberry Pi OS: VNC/SSH pueden configurarse con raspi-config en el instalador"
  fi
  return "$had_fail"
}
