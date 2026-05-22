#!/usr/bin/env bash
# Despliegue y arranque de Kanvis Edge (tras install.sh y edición de config).
set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-/opt/kanvis-edge}"
ENV_SYSTEM="/etc/kanvis-edge/env"
APP_ENV="${INSTALL_ROOT}/.env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/ui.sh
source "${SCRIPT_DIR}/lib/ui.sh"
# shellcheck source=lib/deploy-config.sh
source "${SCRIPT_DIR}/lib/deploy-config.sh"
# shellcheck source=lib/install-access.sh
source "${SCRIPT_DIR}/lib/install-access.sh"

SKIP_PAUSE=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) SKIP_PAUSE=1 ;;
    --help|-h)
      echo "Uso: sudo $0 [--yes]"
      echo "  Pausa para revisar .env, audita valores y arranca servicios."
      exit 0
      ;;
  esac
done

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  ui_need_root "$0" "$@"
  exit 0
fi

if [[ ! -d "$INSTALL_ROOT" ]] || [[ ! -x "${INSTALL_ROOT}/.venv/bin/python" ]]; then
  ui_fail "No está instalado en ${INSTALL_ROOT}. Ejecuta primero: sudo ./scripts/install.sh"
  exit 1
fi

ui_banner "Kanvis Edge — Despliegue"

if [[ "$SKIP_PAUSE" -eq 0 ]]; then
  deploy_wait_for_user_config "$APP_ENV" "$ENV_SYSTEM"
fi

deploy_audit_config "$APP_ENV" "$ENV_SYSTEM"
if [[ "${DEPLOY_CONFIG_ISSUES:-0}" -gt 0 ]]; then
  echo ""
  ui_warn "Hay ${DEPLOY_CONFIG_ISSUES} valor(es) sin configurar. Puedes continuar bajo tu responsabilidad."
  read -r -p "¿Continuar igualmente? [s/N] " confirm
  if [[ ! "${confirm,,}" =~ ^(s|si|sí|y|yes)$ ]]; then
    ui_fail "Abortado. Edita la configuración y vuelve a ejecutar deploy.sh"
    exit 1
  fi
fi

ui_section "Sincronizando configuración desde el repositorio (opcional)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ -f "${REPO_ROOT}/src/main.py" ]]; then
  ui_detail "Actualizando código en ${INSTALL_ROOT}…"
  ui_detail "No se tocan cameras.json, cameras.db ni horario (datos del guardia)"
  rsync -a "${DEPLOY_RSYNC_EXCLUDES[@]}" "${REPO_ROOT}/" "${INSTALL_ROOT}/"
  deploy_seed_config_if_missing "$INSTALL_ROOT" "$REPO_ROOT"
  chown -R kanvis:kanvis "${INSTALL_ROOT}" 2>/dev/null || chown -R "${KANVIS_USER:-kanvis}:${KANVIS_USER:-kanvis}" "${INSTALL_ROOT}"
  find "${INSTALL_ROOT}/src" -type d -name __pycache__ -print0 2>/dev/null \
    | xargs -0 rm -rf 2>/dev/null || true
  ui_ok "Código actualizado"
  KANVIS_USER="${KANVIS_USER:-kanvis}"
  if [[ -f "${INSTALL_ROOT}/requirements.txt" ]]; then
    ui_detail "Actualizando dependencias Python (aiortc, etc.)…"
    sudo -u "$KANVIS_USER" "${INSTALL_ROOT}/.venv/bin/pip" install -q -r "${INSTALL_ROOT}/requirements.txt"
    ui_ok "Dependencias actualizadas"
  fi
else
  ui_detail "Sin repo adjunto; se usa la instalación existente"
fi

ui_section "Arrancando red (AP / DHCP)"
systemctl start kanvis-network.service
sleep 2
if systemctl is-active --quiet kanvis-network.service; then
  ui_ok "kanvis-network activo"
else
  ui_warn "kanvis-network no activo (revisa journalctl -u kanvis-network; puede ser normal en lan_only)"
fi

ui_section "Reiniciando gateway Kanvis Edge"
systemctl restart kanvis-edge.service

ui_section "Comprobando API local (gateway en este cacharro)"
API_PORT="$(read_env_var EDGE_API_PORT "$ENV_SYSTEM" "$APP_ENV" 2>/dev/null || echo 8000)"
API_HOST="$(read_env_var EDGE_API_HOST "$ENV_SYSTEM" "$APP_ENV" 2>/dev/null || echo 0.0.0.0)"
HEALTH_URL="http://127.0.0.1:${API_PORT}/api/v1/health"
AP_IP="$(read_env_var AP_IP "$ENV_SYSTEM" "$APP_ENV" 2>/dev/null || echo 192.168.192.192)"
DEVICE_ID="$(read_env_var DEVICE_ID "$APP_ENV" 2>/dev/null || echo edge)"
WEB_USER="$(read_env_var WEBUI_USERNAME "$APP_ENV" 2>/dev/null || echo admin)"
if [[ "${API_HOST}" == "127.0.0.1" || "${API_HOST}" == "localhost" ]]; then
  ui_warn "EDGE_API_HOST=${API_HOST} — el móvil en el AP no podrá abrir el panel; usa EDGE_API_HOST=0.0.0.0"
fi
ok=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -fsS "$HEALTH_URL" &>/dev/null; then
    ok=1
    break
  fi
  if ! systemctl is-active --quiet kanvis-edge.service 2>/dev/null; then
    ui_detail "kanvis-edge no está active (intento ${i}/15)…"
  else
    ui_detail "Esperando API… (${i}/15)"
  fi
  sleep 2
done

if [[ "$ok" -eq 1 ]]; then
  ui_ok "API local respondiendo en ${HEALTH_URL}"
else
  ui_fail "La API local no responde (no es el backend Kanvis en la nube)"
  ui_detail "Revisa el servicio en ESTE equipo:"
  echo ""
  systemctl status kanvis-edge.service --no-pager -l 2>/dev/null | tail -20 || true
  echo ""
  journalctl -u kanvis-edge -n 25 --no-pager 2>/dev/null || true
  ui_detail "Prueba: curl -v ${HEALTH_URL}"
  ui_detail "Si ProtectSystem bloqueaba el arranque: sudo cp deploy/systemd/kanvis-edge.service /etc/systemd/system/ && sudo systemctl daemon-reload"
  exit 1
fi

if systemctl is-active --quiet kanvis-vnc.service 2>/dev/null; then
  ui_ok "Servicio VNC activo"
fi

echo ""
ui_banner "Despliegue completado"
echo -e "  ${UI_GREEN}Panel web:${UI_NC}"
echo -e "    LAN/AP:  ${UI_BOLD}http://${AP_IP}:${API_PORT}/${UI_NC}  (móvil en WiFi kanvis)"
echo -e "    Local:   ${UI_BOLD}http://127.0.0.1:${API_PORT}/${UI_NC}"
echo -e "    ${UI_DIM}Usa http (no https). EDGE_API_HOST debe ser 0.0.0.0 para acceso desde el AP.${UI_NC}"
echo ""
echo -e "  ${UI_GREEN}Login panel:${UI_NC} usuario ${UI_BOLD}${WEB_USER}${UI_NC} + WEBUI_PASSWORD (.env)"
echo -e "  ${UI_GREEN}WiFi instalación:${UI_NC} kanvis-${DEVICE_ID} (si NETWORK_MODE incluye AP)"
echo ""
ui_detail "Cámaras guardadas en ${INSTALL_ROOT}/config/cameras.json (no se borran en deploy)."
ui_detail "Añade cámaras en la pestaña «Cámaras», elige marca (annke, …) y prueba en «Probar»."
ui_detail "Logs: journalctl -u kanvis-edge -f"
echo ""
