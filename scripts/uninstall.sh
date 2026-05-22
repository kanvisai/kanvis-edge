#!/usr/bin/env bash
# Desinstalación nativa de Kanvis Edge (servicios, ficheros, red AP).
# No desinstala paquetes APT (ffmpeg, hostapd, etc.).
#
# Por defecto conserva el usuario kanvis y la config SSH (99-kanvis-edge.conf)
# para seguir accediendo por SSH tras reinstalar.
#
# Uso:
#   sudo ./scripts/uninstall.sh              # pide confirmación
#   sudo ./scripts/uninstall.sh --yes        # sin preguntar
#   sudo ./scripts/uninstall.sh --remove-user   # borra también el usuario kanvis
set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-/opt/kanvis-edge}"
ENV_SYSTEM="/etc/kanvis-edge/env"
KANVIS_USER="${KANVIS_USER:-kanvis}"
STATE_DIR="/run/kanvis-edge"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/ui.sh
source "${SCRIPT_DIR}/lib/ui.sh" 2>/dev/null || true
# shellcheck source=lib/distro.sh
source "${SCRIPT_DIR}/lib/distro.sh"

KEEP_USER=1
KEEP_SSH=1
ASSUME_YES=0
REMOVE_USER=0

usage() {
  cat <<'EOF'
Kanvis Edge — desinstalación

  sudo uninstall.sh [opciones]

Opciones:
  -y, --yes           No pedir confirmación
  --remove-user       Elimina también el usuario kanvis (y su home)
  --keep-user         Conserva usuario kanvis (por defecto)
  --remove-ssh        Quita /etc/ssh/sshd_config.d/99-kanvis-edge.conf
  -h, --help          Esta ayuda

Qué elimina:
  • Servicios systemd: kanvis-edge, kanvis-network, kanvis-vnc
  • /opt/kanvis-edge y /etc/kanvis-edge
  • Estado AP en /run/kanvis-edge
  • Ajustes de red Kanvis (hostapd/dnsmasq en ejecución, NM en WiFi)

Qué NO elimina (por defecto):
  • Paquetes APT (python3, hostapd, ffmpeg, …)
  • Usuario kanvis ni OpenSSH
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1 ;;
    --remove-user) REMOVE_USER=1; KEEP_USER=0 ;;
    --keep-user) KEEP_USER=1; REMOVE_USER=0 ;;
    --remove-ssh) KEEP_SSH=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

log() {
  if declare -F ui_detail &>/dev/null; then ui_detail "$*"; else echo "[uninstall] $*"; fi
}
ok() {
  if declare -F ui_ok &>/dev/null; then ui_ok "$*"; else echo "[uninstall] OK: $*"; fi
}
warn() {
  if declare -F ui_warn &>/dev/null; then ui_warn "$*"; else echo "[uninstall] AVISO: $*"; fi
}

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    if declare -F ui_need_root &>/dev/null; then
      ui_need_root "$0" "$@"
      exit 0
    fi
    echo "Ejecutar como root: sudo $0" >&2
    exit 1
  fi
}

read_env_var_local() {
  local key="$1" f="$2"
  [[ -f "$f" ]] || return 1
  local line k v
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" && "$line" == *"="* ]] || continue
    k="${line%%=*}"
    k="${k%"${k##*[![:space:]]}"}"
    [[ "$k" == "$key" ]] || continue
    v="${line#*=}"
    v="${v#"${v%%[![:space:]]*}"}"
    v="${v%"${v##*[![:space:]]}"}"
    v="${v%\"}"; v="${v#\"}"
    printf '%s' "$v"
    return 0
  done < "$f"
}

detect_wlan_iface() {
  local iface="${1:-}"
  if [[ -n "$iface" ]] && ip link show "$iface" &>/dev/null; then
    printf '%s' "$iface"
    return 0
  fi
  for iface in wlP1p1s0 wlp1s0 wlan0 wlan1; do
    if ip link show "$iface" &>/dev/null; then
      printf '%s' "$iface"
      return 0
    fi
  done
  return 1
}

stop_kanvis_services() {
  log "Deteniendo servicios systemd…"
  for unit in kanvis-edge kanvis-network kanvis-vnc; do
    systemctl stop "${unit}.service" 2>/dev/null || true
    systemctl disable "${unit}.service" 2>/dev/null || true
  done
}

remove_systemd_units() {
  log "Eliminando unidades systemd…"
  local f
  for f in \
    /etc/systemd/system/kanvis-edge.service \
    /etc/systemd/system/kanvis-network.service \
    /etc/systemd/system/kanvis-vnc.service; do
    rm -f "$f"
  done
  systemctl daemon-reload
  systemctl reset-failed 2>/dev/null || true
}

stop_ap_processes() {
  log "Deteniendo AP (hostapd / dnsmasq Kanvis)…"
  if [[ -x "${INSTALL_ROOT}/scripts/kanvis-network.sh" ]]; then
    "${INSTALL_ROOT}/scripts/kanvis-network.sh" stop 2>/dev/null || true
  fi
  pkill -f "hostapd.*/run/kanvis-edge" 2>/dev/null || true
  pkill -f "dnsmasq.*/run/kanvis-edge" 2>/dev/null || true
  pkill -x hostapd 2>/dev/null || true
  pkill -x dnsmasq 2>/dev/null || true
}

restore_system_network() {
  local iface
  iface="$(detect_wlan_iface "$(read_env_var_local WLAN_INTERFACE "$ENV_SYSTEM" 2>/dev/null || true)")" || iface=""

  if [[ -n "$iface" ]]; then
    log "Restaurando interfaz WiFi ${iface}…"
    ip addr flush dev "$iface" 2>/dev/null || true
    ip link set "$iface" up 2>/dev/null || true
    restore_kanvis_ap_network "$iface"
    if command -v nmcli &>/dev/null; then
      nmcli radio wifi on 2>/dev/null || true
      nmcli dev set "$iface" managed yes 2>/dev/null || true
    fi
  else
    warn "No se detectó interfaz WiFi; revisa la red manualmente si hace falta"
  fi

  log "Reactivando hostapd/dnsmasq del sistema (si estaban enmascarados)…"
  for svc in dnsmasq hostapd; do
    systemctl unmask "${svc}.service" 2>/dev/null || true
    # No los arrancamos: solo desenmascaramos por si el admin los quiere usar
  done
}

cleanup_dhcpcd_kanvis() {
  local f="/etc/dhcpcd.conf"
  [[ -f "$f" ]] || return 0
  if grep -q "Kanvis Edge AP" "$f" 2>/dev/null; then
    log "Quitando entradas Kanvis en ${f}…"
    sed -i '/# Kanvis Edge AP/d' "$f"
    sed -i '/^denyinterfaces /d' "$f"
    systemctl restart dhcpcd 2>/dev/null || true
  fi
}

relocate_kanvis_home() {
  local new_home="/home/${KANVIS_USER}"
  if ! id "$KANVIS_USER" &>/dev/null; then
    return 0
  fi
  local cur_home
  cur_home="$(getent passwd "$KANVIS_USER" | cut -d: -f6)"
  if [[ "$cur_home" == "$new_home" && -d "$new_home" ]]; then
    ok "Usuario ${KANVIS_USER}: home ya en ${new_home}"
    return 0
  fi
  if [[ "$cur_home" == "$INSTALL_ROOT" || ! -d "$cur_home" ]]; then
    log "Usuario ${KANVIS_USER}: home → ${new_home} (para poder borrar ${INSTALL_ROOT})"
    mkdir -p "$new_home"
    chown "${KANVIS_USER}:${KANVIS_USER}" "$new_home"
    chmod 700 "$new_home"
    usermod -d "$new_home" "$KANVIS_USER"
    ok "Home del usuario movido a ${new_home}"
  fi
}

remove_kanvis_user() {
  if ! id "$KANVIS_USER" &>/dev/null; then
    return 0
  fi
  log "Eliminando usuario ${KANVIS_USER}…"
  local home
  home="$(getent passwd "$KANVIS_USER" | cut -d: -f6)"
  userdel -r "$KANVIS_USER" 2>/dev/null || userdel "$KANVIS_USER" 2>/dev/null || true
  if [[ -n "$home" && -d "$home" && "$home" != "/" ]]; then
    rm -rf "$home"
  fi
}

remove_install_tree() {
  log "Eliminando ${INSTALL_ROOT}…"
  rm -rf "$INSTALL_ROOT"
  ok "Árbol de instalación eliminado"
}

remove_config() {
  log "Eliminando /etc/kanvis-edge…"
  rm -rf /etc/kanvis-edge
  ok "Configuración del sistema eliminada"
}

remove_runtime_state() {
  rm -rf "$STATE_DIR"
}

remove_ssh_dropin() {
  local f="/etc/ssh/sshd_config.d/99-kanvis-edge.conf"
  [[ -f "$f" ]] || return 0
  log "Eliminando ${f}…"
  rm -f "$f"
  systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
}

main() {
  need_root "$@"

  if declare -F ui_banner &>/dev/null; then
    ui_banner "Kanvis Edge — Desinstalación"
  else
    echo "=== Kanvis Edge — Desinstalación ==="
  fi

  if [[ "$ASSUME_YES" -ne 1 ]]; then
    echo ""
    echo "Se eliminarán servicios y ficheros de Kanvis Edge en este equipo."
    if [[ "$KEEP_USER" -eq 1 ]]; then
      echo "  • Se conserva el usuario ${KANVIS_USER} y SSH (acceso remoto)."
    else
      echo "  • Se eliminará también el usuario ${KANVIS_USER}."
    fi
    echo "  • Los paquetes APT instalados NO se desinstalan."
    echo ""
    read -r -p "¿Continuar? [s/N] " ans
    case "${ans,,}" in
      s|si|sí|y|yes) ;;
      *) echo "Cancelado."; exit 0 ;;
    esac
  fi

  stop_kanvis_services
  stop_ap_processes
  restore_system_network
  cleanup_dhcpcd_kanvis

  if [[ "$KEEP_USER" -eq 1 ]]; then
    relocate_kanvis_home
  fi

  remove_systemd_units
  remove_runtime_state

  if [[ -d "$INSTALL_ROOT" ]]; then
    remove_install_tree
  else
    warn "No existe ${INSTALL_ROOT}"
  fi

  if [[ -d /etc/kanvis-edge ]]; then
    remove_config
  fi

  if [[ "$KEEP_SSH" -eq 0 ]]; then
    remove_ssh_dropin
  else
    ok "SSH y 99-kanvis-edge.conf conservados"
  fi

  if [[ "$REMOVE_USER" -eq 1 ]]; then
    remove_kanvis_user
  elif [[ "$KEEP_USER" -eq 1 ]]; then
    ok "Usuario ${KANVIS_USER} conservado (ssh ${KANVIS_USER}@<IP>)"
  fi

  echo ""
  if declare -F ui_banner &>/dev/null; then
    ui_banner "Desinstalación completada"
    ui_detail "Reinstalar desde el clone:"
    echo -e "  ${UI_BOLD}cd kanvis-edge && sudo ./scripts/install.sh${UI_NC}"
    echo -e "  ${UI_BOLD}sudo ./scripts/deploy.sh${UI_NC}"
    ui_detail "Recomendado: Ethernet + SSH; luego NETWORK_MODE=ap_and_lan si necesitas AP."
  else
    echo "Listo. Reinstala con: sudo ./scripts/install.sh"
  fi
  echo ""
}

main "$@"
