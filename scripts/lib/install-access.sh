#!/usr/bin/env bash
# Usuario kanvis (login + sudo), SSH con reenvío X11 y VNC.
# shellcheck disable=SC2034
set -euo pipefail

install_access_log() { echo "[install-access] $*"; }

# Lee VAR=valor de uno o más ficheros estilo .env (sin eval).
read_env_var() {
  local key="$1"
  shift
  local f line k v
  for f in "$@"; do
    [[ -f "$f" ]] || continue
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%%#*}"
      line="${line#"${line%%[![:space:]]*}"}"
      line="${line%"${line##*[![:space:]]}"}"
      [[ -n "$line" ]] || continue
      [[ "$line" == *"="* ]] || continue
      k="${line%%=*}"
      k="${k%"${k##*[![:space:]]}"}"
      [[ "$k" == "$key" ]] || continue
      v="${line#*=}"
      v="${v#"${v%%[![:space:]]*}"}"
      v="${v%"${v##*[![:space:]]}"}"
      v="${v%\"}"; v="${v#\"}"
      v="${v%\'}"; v="${v#\'}"
      printf '%s' "$v"
      return 0
    done < "$f"
  done
  return 1
}

resolve_kanvis_os_password() {
  local env_system="$1" app_env="$2"
  local pw
  pw="$(read_env_var KANVIS_OS_PASSWORD "$env_system" "$app_env" 2>/dev/null || true)"
  if [[ -z "$pw" || "$pw" == "change-me-on-install" ]]; then
    pw="$(openssl rand -base64 18 | tr -d '/+=' | head -c 16)"
    install_access_log "KANVIS_OS_PASSWORD no definida; generada contraseña temporal (guárdala): ${pw}"
    install_access_log "  Añádela en ${env_system} como KANVIS_OS_PASSWORD=..."
  fi
  printf '%s' "$pw"
}

setup_kanvis_login_user() {
  local user="$1" install_root="$2" password="$3"

  if id "$user" &>/dev/null; then
    install_access_log "Usuario ${user} existente; actualizando shell, home y sudo"
    usermod -d "$install_root" -s /bin/bash -aG sudo "$user" 2>/dev/null || usermod -d "$install_root" -s /bin/bash "$user"
  else
    install_access_log "Creando usuario ${user} (home ${install_root})"
    if [[ -d "$install_root" ]]; then
      useradd -d "$install_root" -s /bin/bash -G sudo "$user"
    else
      useradd -m -d "$install_root" -s /bin/bash -G sudo "$user"
    fi
  fi

  echo "${user}:${password}" | chpasswd
  install_access_log "Contraseña OS aplicada para ${user} (sudo habilitado)"
}

setup_ssh_x11() {
  local enabled="${1:-true}"
  if [[ "$enabled" != "true" && "$enabled" != "1" && "$enabled" != "yes" ]]; then
    install_access_log "SSH omitido (KANVIS_ENABLE_SSH=${enabled})"
    return 0
  fi

  install_access_log "Instalando OpenSSH y habilitando reenvío X11 (+X)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    openssh-server \
    xauth \
    x11-apps \
    dbus-x11 \
    2>/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server xauth dbus-x11

  mkdir -p /etc/ssh/sshd_config.d
  cat > /etc/ssh/sshd_config.d/99-kanvis-edge.conf <<'EOF'
# Kanvis Edge — acceso remoto instalación/soporte
X11Forwarding yes
X11DisplayOffset 10
X11UseLocalhost yes
AddressFamily any
EOF

  if command -v raspi-config &>/dev/null; then
    raspi-config nonint do_ssh 0 2>/dev/null || true
  fi

  systemctl enable ssh 2>/dev/null || systemctl enable sshd 2>/dev/null || true
  systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
  install_access_log "SSH activo (ssh -X kanvis@<IP> para apps gráficas remotas)"
}

setup_vnc_tigervnc() {
  local user="$1" install_root="$2" password="$3" display="${4:-:1}"

  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tigervnc-standalone-server 2>/dev/null || true
  if ! command -v vncserver &>/dev/null; then
    install_access_log "AVISO: vncserver no disponible; instala tigervnc manualmente"
    return 0
  fi

  local vnc_dir="${install_root}/.vnc"
  install -d -o "$user" -g "$user" -m 700 "$vnc_dir"
  printf '%s\n' "$password" | sudo -u "$user" vncpasswd -f > "${vnc_dir}/passwd"
  chmod 600 "${vnc_dir}/passwd"
  chown "$user:$user" "${vnc_dir}/passwd"

  if [[ ! -f "${vnc_dir}/xstartup" ]]; then
    cat > "${vnc_dir}/xstartup" <<'EOF'
#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
[ -r "$HOME/.Xresources" ] && xrdb "$HOME/.Xresources"
xsetroot -solid grey
xterm -geometry 100x30+10+10 -ls &
EOF
    chmod +x "${vnc_dir}/xstartup"
    chown "$user:$user" "${vnc_dir}/xstartup"
  fi

  local unit="/etc/systemd/system/kanvis-vnc.service"
  cat > "$unit" <<EOF
[Unit]
Description=TigerVNC for Kanvis Edge (${display})
After=network.target

[Service]
Type=forking
User=${user}
WorkingDirectory=${install_root}
PIDFile=${install_root}/.vnc/${display#:}.pid
ExecStart=/usr/bin/vncserver ${display} -geometry 1280x720 -depth 24 -localhost no
ExecStop=/usr/bin/vncserver -kill ${display}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable kanvis-vnc.service
  systemctl restart kanvis-vnc.service 2>/dev/null || install_access_log "VNC: arranca con systemctl start kanvis-vnc tras el primer boot"
  install_access_log "TigerVNC en ${display} (visor → <IP>${display})"
}

setup_vnc() {
  local distro="$1" user="$2" install_root="$3" password="$4" enabled="${5:-true}" display="${6:-:1}"

  if [[ "$enabled" != "true" && "$enabled" != "1" && "$enabled" != "yes" ]]; then
    install_access_log "VNC omitido (KANVIS_ENABLE_VNC=${enabled})"
    return 0
  fi

  case "$distro" in
    raspberry_pi_os)
      install_access_log "Raspberry Pi OS: VNC del sistema (raspi-config)"
      if command -v raspi-config &>/dev/null; then
        raspi-config nonint do_vnc 0 2>/dev/null || true
      fi
      install_access_log "Conéctate con el usuario ${user} y la contraseña KANVIS_OS_PASSWORD"
      ;;
    *)
      setup_vnc_tigervnc "$user" "$install_root" "$password" "$display"
      ;;
  esac
}

setup_kanvis_remote_access() {
  local user="$1" install_root="$2" env_system="$3" app_env="$4" distro="$5"

  local password ssh_en vnc_en vnc_display
  password="$(resolve_kanvis_os_password "$env_system" "$app_env")"
  ssh_en="$(read_env_var KANVIS_ENABLE_SSH "$env_system" "$app_env" 2>/dev/null || echo true)"
  vnc_en="$(read_env_var KANVIS_ENABLE_VNC "$env_system" "$app_env" 2>/dev/null || echo true)"
  vnc_display="$(read_env_var KANVIS_VNC_DISPLAY "$env_system" "$app_env" 2>/dev/null || echo :1)"

  setup_kanvis_login_user "$user" "$install_root" "$password"
  setup_ssh_x11 "$ssh_en"
  setup_vnc "$distro" "$user" "$install_root" "$password" "$vnc_en" "$vnc_display"
}
