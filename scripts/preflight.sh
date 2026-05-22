#!/usr/bin/env bash
# Preflight Kanvis Edge: dependencias + estimación de capacidad.
# Uso: ./scripts/preflight.sh
#      sudo ./scripts/preflight.sh --install
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ui.sh
source "${SCRIPT_DIR}/lib/ui.sh"
# shellcheck source=lib/distro.sh
source "${SCRIPT_DIR}/lib/distro.sh"
# shellcheck source=lib/preflight-deps.sh
source "${SCRIPT_DIR}/lib/preflight-deps.sh"
# shellcheck source=lib/preflight-capacity.sh
source "${SCRIPT_DIR}/lib/preflight-capacity.sh"

DO_INSTALL=0
DO_FIX_SERVICES=0
for arg in "$@"; do
  case "$arg" in
    --install|-i) DO_INSTALL=1 ;;
    --fix-services|--fix-dns) DO_FIX_SERVICES=1 ;;
    --help|-h)
      echo "Uso: $0 [--install] [--fix-services]"
      echo "  Sin --install: solo comprueba (verde/rojo) y estima capacidad."
      echo "  Con --install: pide sudo, instala paquetes y detiene dnsmasq/hostapd del sistema."
      echo "  --fix-services: solo corrige puerto 53 si apt install dnsmasq ya falló antes."
      exit 0
      ;;
  esac
done

ui_banner "Kanvis Edge — Preflight"

DISTRO="$(detect_kanvis_distro)"
ui_detail "Distro: ${DISTRO}"
ui_detail "Arquitectura: $(uname -m)"

had_fail=0

if [[ "$DO_FIX_SERVICES" -eq 1 ]]; then
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    ui_need_root "$0" --fix-services
    exit 0
  fi
  preflight_stabilize_ap_services
  ui_ok "Corrección aplicada. Sigue con: sudo ./scripts/install.sh"
  exit 0
fi

preflight_show_all_checks || had_fail=1

preflight_scan_missing
if [[ ${#PREFLIGHT_MISSING[@]} -gt 0 ]]; then
  echo ""
  ui_warn "Faltan ${#PREFLIGHT_MISSING[@]} paquete(s): ${PREFLIGHT_MISSING[*]}"
  if [[ "$DO_INSTALL" -eq 1 ]]; then
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
      ui_need_root "$0" --install
      exit 0
    fi
    preflight_install_missing
    ui_section "Verificación tras instalación"
    preflight_show_all_checks || had_fail=1
  else
    ui_detail "Ejecuta: sudo $0 --install"
    ui_detail "O: sudo ./scripts/install.sh (también instala dependencias)"
  fi
else
  ui_ok "Dependencias: todo presente"
fi

IFACE="${LAN_INTERFACE:-eth0}"
preflight_capacity_estimate "$IFACE"

echo ""
if [[ "$had_fail" -eq 0 ]]; then
  ui_ok "Preflight: listo para instalar (./scripts/install.sh)"
else
  ui_warn "Preflight: corrige dependencias antes del instalador"
fi
echo ""
ui_detail "Siguiente paso: sudo ./scripts/install.sh"
ui_detail "Después: edita config y sudo ./scripts/deploy.sh"
echo ""

exit "$had_fail"
