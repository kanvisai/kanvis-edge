#!/usr/bin/env bash
# Instalación NATIVA en hardware (Debian / Raspberry Pi / N100)
# NO combinar con Docker en el mismo equipo para el mismo servicio.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/kanvis-edge}"
KANVIS_USER="${KANVIS_USER:-kanvis}"
ENV_SYSTEM="/etc/kanvis-edge/env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/distro.sh
source "${SCRIPT_DIR}/lib/distro.sh"

log() { echo "[install] $*"; }

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  log "Ejecutar con sudo"
  exit 1
fi

DISTRO="$(detect_kanvis_distro)"
log "Distro detectada: ${DISTRO}"

log "Dependencias del sistema"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3 python3-venv python3-pip \
  ffmpeg \
  hostapd dnsmasq \
  iptables \
  curl \
  rsync \
  || true

case "$DISTRO" in
  raspberry_pi_os)
    log "Ajustes Raspberry Pi OS (hostapd/dnsmasq/wpa_supplicant)"
    systemctl unmask hostapd dnsmasq 2>/dev/null || true
    systemctl stop hostapd dnsmasq 2>/dev/null || true
    systemctl disable hostapd dnsmasq 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq network-manager 2>/dev/null || true
    ;;
  *)
    log "Ajustes Debian/genérico"
    systemctl stop hostapd dnsmasq 2>/dev/null || true
    systemctl disable hostapd dnsmasq 2>/dev/null || true
    ;;
esac

if ! id "$KANVIS_USER" &>/dev/null; then
  useradd --system --home "$INSTALL_ROOT" --shell /usr/sbin/nologin "$KANVIS_USER"
fi

log "Copiando a ${INSTALL_ROOT}"
mkdir -p "$INSTALL_ROOT" /etc/kanvis-edge
rsync -a --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  "$REPO_ROOT/" "$INSTALL_ROOT/"

if [[ ! -f "${INSTALL_ROOT}/.env" ]]; then
  cp "${INSTALL_ROOT}/.env.example" "${INSTALL_ROOT}/.env"
  log "Creado ${INSTALL_ROOT}/.env — edítalo antes de producción"
fi

if [[ ! -f "$ENV_SYSTEM" ]]; then
  cp "${INSTALL_ROOT}/deploy/network/kanvis-edge.env.example" "$ENV_SYSTEM"
  log "Creado ${ENV_SYSTEM}"
fi

mkdir -p "${INSTALL_ROOT}/config/data"
chown -R "${KANVIS_USER}:${KANVIS_USER}" "$INSTALL_ROOT"

log "Entorno Python (nativo, sin Docker)"
sudo -u "$KANVIS_USER" python3 -m venv "${INSTALL_ROOT}/.venv"
sudo -u "$KANVIS_USER" "${INSTALL_ROOT}/.venv/bin/pip" install -q -r "${INSTALL_ROOT}/requirements.txt"

chmod +x "${INSTALL_ROOT}/scripts/kanvis-network.sh"
chmod +x "${INSTALL_ROOT}/scripts/lib/distro.sh" 2>/dev/null || true

install_mediamtx() {
  local dest="${INSTALL_ROOT}/bin/mediamtx"
  if [[ -x "$dest" ]]; then
    log "MediaMTX ya presente en ${dest}"
    return 0
  fi
  local version="v1.11.3"
  local arch=""
  case "$(uname -m)" in
    aarch64|arm64) arch="arm64v8" ;;
    x86_64|amd64) arch="amd64" ;;
    *)
      log "Sin binario MediaMTX para $(uname -m); instálalo manualmente si usas RTSP gateway"
      return 0
      ;;
  esac
  local url="https://github.com/bluenviron/mediamtx/releases/download/${version}/mediamtx_${version}_linux_${arch}.tar.gz"
  local tmp
  tmp="$(mktemp -d)"
  log "Descargando MediaMTX ${version} (${arch})"
  if ! curl -fsSL "$url" -o "${tmp}/mediamtx.tar.gz"; then
    log "AVISO: no se pudo descargar MediaMTX; RTSP gateway requerirá instalación manual"
    rm -rf "$tmp"
    return 0
  fi
  tar -xzf "${tmp}/mediamtx.tar.gz" -C "$tmp"
  mkdir -p "${INSTALL_ROOT}/bin"
  install -m 755 "${tmp}/mediamtx" "$dest"
  chown "${KANVIS_USER}:${KANVIS_USER}" "$dest"
  rm -rf "$tmp"
  log "MediaMTX instalado en ${dest}"
}

install_mediamtx

log "systemd (nativo)"
cp "${INSTALL_ROOT}/deploy/systemd/kanvis-edge.service" /etc/systemd/system/
cp "${INSTALL_ROOT}/deploy/systemd/kanvis-network.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable kanvis-network.service kanvis-edge.service

log ""
log "=== Instalación NATIVA completada ==="
log "Método: systemd + Python en ${INSTALL_ROOT}"
log "NO hace falta Docker para el gateway en este dispositivo."
log ""
log "  1. Edita ${INSTALL_ROOT}/.env (WEBUI_PASSWORD, JWT_SECRET, API_KEY)"
log "  2. Edita ${ENV_SYSTEM} (NETWORK_MODE, AP_PASSWORD)"
log "  3. sudo systemctl start kanvis-network kanvis-edge"
log "  4. WiFi kanvis-XXXX → http://192.168.192.192:8000/"
log ""
log "Docker (opcional): solo si quieres OTRO despliegue sin AP; ver docs/INSTALACION_HARDWARE.md"
