#!/usr/bin/env bash
# Comprobación de configuración antes de arrancar servicios.
set -euo pipefail

deploy_read_env_val() {
  local key="$1" file="$2"
  [[ -f "$file" ]] || return 1
  local line k v
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ "$line" == *"="* ]] || continue
    k="${line%%=*}"
    k="${k%"${k##*[![:space:]]}"}"
    [[ "$k" == "$key" ]] || continue
    v="${line#*=}"
    v="${v#"${v%%[![:space:]]*}"}"
    v="${v%"${v##*[![:space:]]}"}"
    v="${v%\"}"; v="${v#\"}"
    printf '%s' "$v"
    return 0
  done < "$file"
  return 1
}

deploy_is_placeholder() {
  local v="${1,,}"
  case "$v" in
    ""|change-me*|replace-with*|changeme|kanvis-install) return 0 ;;
  esac
  [[ "$v" == *"change-me"* ]] && return 0
  return 1
}

deploy_show_config_checklist() {
  local app_env="$1" sys_env="$2"
  ui_banner "Configuración obligatoria antes del arranque"
  echo -e "${UI_BOLD}Edita estos ficheros y sustituye valores de ejemplo:${UI_NC}"
  echo ""
  echo -e "  ${UI_CYAN}1) ${app_env}${UI_NC}"
  ui_detail "DEVICE_NAME       → nombre exacto del edge en Kanvis (C4)"
  ui_detail "DEVICE_ID         → id corto (SSID kanvis-XXXX)"
  ui_detail "WEBUI_USERNAME    → usuario panel web (p. ej. admin)"
  ui_detail "WEBUI_PASSWORD    → contraseña panel (no 'change-me…')"
  ui_detail "JWT_SECRET        → secreto JWT (aleatorio largo)"
  ui_detail "API_KEY           → clave para que la nube llame al edge"
  ui_detail "CLOUD_REPORT_URL  → URL report-public-ip del backend"
  ui_detail "CLOUD_ACCESS_TOKEN → token del alta en C4 (si CLOUD_REPORT_ENABLED=true)"
  echo ""
  echo -e "  ${UI_CYAN}2) ${sys_env}${UI_NC}"
  ui_detail "KANVIS_OS_PASSWORD → contraseña usuario Linux 'kanvis'"
  ui_detail "AP_PASSWORD        → WiFi de instalación kanvis-XXXX"
  ui_detail "NETWORK_MODE       → ap_and_lan | lan_only | ap_only"
  echo ""
}

deploy_audit_config() {
  local app_env="$1" sys_env="$2"
  local issues=0
  ui_section "Auditoría rápida de valores"
  local keys_app=(
    "WEBUI_PASSWORD:Contraseña panel"
    "JWT_SECRET:JWT secret"
    "API_KEY:API Key"
    "DEVICE_NAME:Nombre dispositivo (nube)"
  )
  local k label v
  for entry in "${keys_app[@]}"; do
    k="${entry%%:*}"
    label="${entry#*:}"
    v="$(deploy_read_env_val "$k" "$app_env" 2>/dev/null || true)"
    if deploy_is_placeholder "$v"; then
      ui_fail "${label} (${k}) sin configurar en .env"
      issues=$((issues + 1))
    else
      ui_ok "${label}"
    fi
  done
  v="$(deploy_read_env_val KANVIS_OS_PASSWORD "$sys_env" 2>/dev/null || deploy_read_env_val KANVIS_OS_PASSWORD "$app_env" 2>/dev/null || true)"
  if deploy_is_placeholder "$v"; then
    ui_fail "KANVIS_OS_PASSWORD (usuario kanvis)"
    issues=$((issues + 1))
  else
    ui_ok "Contraseña usuario kanvis"
  fi
  local cloud_en
  cloud_en="$(deploy_read_env_val CLOUD_REPORT_ENABLED "$app_env" 2>/dev/null || echo false)"
  if [[ "${cloud_en,,}" == "true" ]]; then
    v="$(deploy_read_env_val CLOUD_ACCESS_TOKEN "$app_env" 2>/dev/null || deploy_read_env_val CLOUD_REPORT_TOKEN "$app_env" 2>/dev/null || true)"
    if deploy_is_placeholder "$v"; then
      ui_fail "CLOUD_ACCESS_TOKEN (reporte IP activado)"
      issues=$((issues + 1))
    else
      ui_ok "Token reporte IP (CLOUD_ACCESS_TOKEN)"
    fi
  fi
  DEPLOY_CONFIG_ISSUES="$issues"
}

deploy_wait_for_user_config() {
  deploy_show_config_checklist "$@"
  echo ""
  echo -e "${UI_YELLOW}Detén el script, edita los ficheros anteriores y vuelve a ejecutar deploy.${UI_NC}"
  echo ""
  read -r -p "Pulsa ENTER cuando hayas guardado los cambios para continuar (Ctrl+C para abortar)… " _
}
