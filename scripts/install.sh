#!/usr/bin/env bash
# Instalación NATIVA interactiva (Debian / Raspberry Pi / N100)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/kanvis-edge}"
KANVIS_USER="${KANVIS_USER:-kanvis}"
ENV_SYSTEM="/etc/kanvis-edge/env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/ui.sh
source "${SCRIPT_DIR}/lib/ui.sh"
# shellcheck source=lib/distro.sh
source "${SCRIPT_DIR}/lib/distro.sh"
# shellcheck source=lib/preflight-deps.sh
source "${SCRIPT_DIR}/lib/preflight-deps.sh"
# shellcheck source=lib/install-access.sh
source "${SCRIPT_DIR}/lib/install-access.sh"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  ui_banner "Kanvis Edge — Instalador"
  ui_need_root "$0" "$@"
  exit 0
fi

ui_banner "Kanvis Edge — Instalador nativo"
ui_detail "Origen: ${REPO_ROOT}"
ui_detail "Destino: ${INSTALL_ROOT}"

DISTRO="$(detect_kanvis_distro)"
ui_ok "Distro detectada: ${DISTRO}"

ui_section "Comprobando dependencias del sistema"
if ! preflight_show_all_checks; then
  ui_section "Instalando dependencias faltantes"
  preflight_install_missing
  preflight_show_all_checks || true
fi
ui_ok "Dependencias del sistema listas"

case "$DISTRO" in
  raspberry_pi_os)
    ui_section "Ajustes Raspberry Pi OS"
    ui_detail "Deshabilitando hostapd/dnsmasq del sistema (los gestiona kanvis-network)"
    systemctl unmask hostapd dnsmasq 2>/dev/null || true
    systemctl stop hostapd dnsmasq 2>/dev/null || true
    systemctl disable hostapd dnsmasq 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq network-manager 2>/dev/null || true
    ui_ok "Perfil Raspberry Pi OS aplicado"
    ;;
  *)
    ui_section "Ajustes red Debian/genérico"
    systemctl stop hostapd dnsmasq 2>/dev/null || true
    systemctl disable hostapd dnsmasq 2>/dev/null || true
    ui_ok "Servicios hostapd/dnsmasq del sistema deshabilitados"
    ;;
esac

ui_section "Copiando aplicación a ${INSTALL_ROOT}"
mkdir -p "$INSTALL_ROOT" /etc/kanvis-edge
ui_detail "Sincronizando archivos del repositorio…"
rsync -a --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  "$REPO_ROOT/" "$INSTALL_ROOT/"
ui_ok "Archivos copiados"

ui_section "Ficheros de configuración (un solo fichero en hardware)"
if [[ ! -f "$ENV_SYSTEM" ]]; then
  cp "${INSTALL_ROOT}/deploy/kanvis-edge.env.example" "$ENV_SYSTEM"
  ui_ok "Creado ${ENV_SYSTEM}"
else
  ui_warn "Ya existe ${ENV_SYSTEM} (no se sobrescribe)"
fi
ln -sfn "$ENV_SYSTEM" "${INSTALL_ROOT}/.env"
ui_ok "Enlace ${INSTALL_ROOT}/.env → ${ENV_SYSTEM}"
ui_detail "Edita solo: sudo nano ${ENV_SYSTEM}"
mkdir -p "${INSTALL_ROOT}/config/data" "${INSTALL_ROOT}/config/brands"

ui_section "Contraseña usuario Linux (kanvis)"
ui_detail "Puedes definirla ANTES del install en el clone:"
ui_detail "  deploy/kanvis-edge.env.example  →  KANVIS_OS_PASSWORD (antes del install)"
ui_detail "Si no, se genera una temporal y se muestra aquí (cópiala)."
KANVIS_OS_PW="$(resolve_kanvis_os_password "$ENV_SYSTEM" "${INSTALL_ROOT}/.env")" || {
  ui_fail "No se pudo obtener KANVIS_OS_PASSWORD"
  exit 1
}
if [[ -z "$KANVIS_OS_PW" ]]; then
  ui_fail "Contraseña vacía. Edita ${ENV_SYSTEM} y vuelve a ejecutar install.sh"
  exit 1
fi
SSH_EN="$(read_env_var KANVIS_ENABLE_SSH "$ENV_SYSTEM" "${INSTALL_ROOT}/.env" 2>/dev/null || echo true)"
VNC_EN="$(read_env_var KANVIS_ENABLE_VNC "$ENV_SYSTEM" "${INSTALL_ROOT}/.env" 2>/dev/null || echo true)"
VNC_DISP="$(read_env_var KANVIS_VNC_DISPLAY "$ENV_SYSTEM" "${INSTALL_ROOT}/.env" 2>/dev/null || echo :1)"

ui_section "Creando usuario ${KANVIS_USER}"
ui_detail "Usuario con shell bash, grupo sudo y home en ${INSTALL_ROOT}"
setup_kanvis_login_user "$KANVIS_USER" "$INSTALL_ROOT" "$KANVIS_OS_PW"
ui_ok "Usuario ${KANVIS_USER} creado"

ui_section "Habilitando acceso SSH para ${KANVIS_USER}"
setup_ssh_x11 "$SSH_EN"

ui_section "Habilitando VNC"
setup_vnc "$DISTRO" "$KANVIS_USER" "$INSTALL_ROOT" "$KANVIS_OS_PW" "$VNC_EN" "$VNC_DISP"

ui_section "Permisos y entorno Python"
chown -R "${KANVIS_USER}:${KANVIS_USER}" "$INSTALL_ROOT"
ui_detail "Creando venv en ${INSTALL_ROOT}/.venv"
sudo -u "$KANVIS_USER" python3 -m venv "${INSTALL_ROOT}/.venv"
ui_detail "Instalando dependencias pip (puede tardar unos minutos)…"
sudo -u "$KANVIS_USER" "${INSTALL_ROOT}/.venv/bin/pip" install -q -r "${INSTALL_ROOT}/requirements.txt"
ui_ok "Entorno Python instalado"

ui_section "Scripts y binarios auxiliares"
chmod +x "${INSTALL_ROOT}/scripts/kanvis-network.sh"
chmod +x "${INSTALL_ROOT}/scripts/"*.sh 2>/dev/null || true
chmod +x "${INSTALL_ROOT}/scripts/lib/"*.sh 2>/dev/null || true

install_mediamtx() {
  local dest="${INSTALL_ROOT}/bin/mediamtx"
  if [[ -x "$dest" ]]; then
    ui_ok "MediaMTX ya presente"
    return 0
  fi
  local version="v1.11.3" arch=""
  case "$(uname -m)" in
    aarch64|arm64) arch="arm64v8" ;;
    x86_64|amd64) arch="amd64" ;;
    *)
      ui_warn "Sin binario MediaMTX para $(uname -m); opcional para RTSP gateway"
      return 0
      ;;
  esac
  local url="https://github.com/bluenviron/mediamtx/releases/download/${version}/mediamtx_${version}_linux_${arch}.tar.gz"
  local tmp
  tmp="$(mktemp -d)"
  ui_detail "Descargando MediaMTX ${version} (${arch})…"
  if ! curl -fsSL "$url" -o "${tmp}/mediamtx.tar.gz"; then
    ui_warn "No se pudo descargar MediaMTX (instalación manual si usas gateway)"
    rm -rf "$tmp"
    return 0
  fi
  tar -xzf "${tmp}/mediamtx.tar.gz" -C "$tmp"
  mkdir -p "${INSTALL_ROOT}/bin"
  install -m 755 "${tmp}/mediamtx" "$dest"
  chown "${KANVIS_USER}:${KANVIS_USER}" "$dest"
  rm -rf "$tmp"
  ui_ok "MediaMTX instalado"
}

ui_section "MediaMTX (RTSP gateway, opcional)"
install_mediamtx

ui_section "Servicios systemd"
cp "${INSTALL_ROOT}/deploy/systemd/kanvis-edge.service" /etc/systemd/system/
cp "${INSTALL_ROOT}/deploy/systemd/kanvis-network.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable kanvis-network.service kanvis-edge.service
ui_ok "Servicios kanvis-network y kanvis-edge habilitados al arranque"
ui_detail "Los servicios NO se inician aquí; usa deploy.sh tras editar la config"

echo ""
ui_banner "Instalación completada"
ui_ok "Software instalado en ${INSTALL_ROOT}"
echo ""
ui_detail "Siguiente paso obligatorio:"
echo -e "  ${UI_BOLD}sudo ${INSTALL_ROOT}/scripts/deploy.sh${UI_NC}"
echo ""
ui_detail "Ahí pausará para que edites .env y /etc/kanvis-edge/env, y luego arrancará el gateway."
AP_IP_HINT="$(read_env_var AP_IP "$ENV_SYSTEM" "${INSTALL_ROOT}/.env" 2>/dev/null || echo 192.168.192.192)"
ui_detail "Panel (tras deploy): http://${AP_IP_HINT}:8000/"
echo ""
