#!/usr/bin/env bash
# Salida visual para instalador / preflight / deploy (colores si hay TTY).
# shellcheck disable=SC2034

if [[ -t 1 ]]; then
  UI_NC=$'\033[0m'
  UI_BOLD=$'\033[1m'
  UI_DIM=$'\033[2m'
  UI_GREEN=$'\033[32m'
  UI_RED=$'\033[31m'
  UI_YELLOW=$'\033[33m'
  UI_CYAN=$'\033[36m'
  UI_BLUE=$'\033[34m'
else
  UI_NC="" UI_BOLD="" UI_DIM="" UI_GREEN="" UI_RED="" UI_YELLOW="" UI_CYAN="" UI_BLUE=""
fi

ui_banner() {
  echo ""
  echo -e "${UI_BOLD}${UI_CYAN}══════════════════════════════════════════════════════════════${UI_NC}"
  echo -e "${UI_BOLD}${UI_CYAN}  $*${UI_NC}"
  echo -e "${UI_BOLD}${UI_CYAN}══════════════════════════════════════════════════════════════${UI_NC}"
  echo ""
}

ui_section() {
  echo ""
  echo -e "${UI_BOLD}${UI_BLUE}-- $* --${UI_NC}"
}

ui_detail() {
  echo -e "  ${UI_DIM}$*${UI_NC}"
}

ui_ok() {
  echo -e "  ${UI_GREEN}✓ $*${UI_NC}"
}

ui_fail() {
  echo -e "  ${UI_RED}✗ $*${UI_NC}"
}

ui_warn() {
  echo -e "  ${UI_YELLOW}⚠ $*${UI_NC}"
}

ui_check_line() {
  local label="$1" status="$2"
  if [[ "$status" == "ok" ]]; then
    printf "  %-42s " "$label"
    echo -e "${UI_GREEN}[OK]${UI_NC}"
  else
    printf "  %-42s " "$label"
    echo -e "${UI_RED}[FALTA]${UI_NC}"
  fi
}

ui_need_root() {
  ui_banner "Permisos de administrador"
  ui_warn "Para instalar paquetes del sistema hace falta ejecutar como root."
  ui_detail "Se pedirá la contraseña de sudo y el script continuará con privilegios elevados."
  echo ""
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    return 0
  fi
  if ! sudo -v; then
    ui_fail "No se obtuvo sudo. Abortando."
    exit 1
  fi
  ui_ok "Credenciales sudo válidas"
  exec sudo -E bash "$@"
}
