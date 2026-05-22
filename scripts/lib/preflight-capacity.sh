#!/usr/bin/env bash
# Estimación pesimista de cámaras RTSP relay/broadcast simultáneas en el edge.
set -euo pipefail

# Supuestos conservadores (720p H.264 ~20 fps, ingest + relay copy)
CAP_MB_PER_CAMERA=150
CAP_CPU_SHARE_PER_CAMERA=0.18
CAP_MBPS_LAN_PER_CAMERA=6
CAP_CPU_USABLE_RATIO=0.55
CAP_RAM_USABLE_RATIO=0.60
CAP_BW_USABLE_RATIO=0.35
CAP_SAFETY_FACTOR=0.75

preflight_read_mem_available_mb() {
  local avail kb
  avail="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  if [[ "$avail" -lt 100000 ]]; then
    avail="$(awk '/MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 4194304)"
    avail=$((avail / 2))
  fi
  echo $((avail / 1024))
}

preflight_read_cpu_cores() {
  nproc 2>/dev/null || echo 2
}

preflight_detect_lan_mbps() {
  local iface="${1:-eth0}" speed mbits
  if [[ -d "/sys/class/net/${iface}" ]]; then
    speed="$(cat "/sys/class/net/${iface}/speed" 2>/dev/null || echo 0)"
    if [[ "$speed" =~ ^[0-9]+$ ]] && [[ "$speed" -ge 10 ]]; then
      echo "$speed"
      return 0
    fi
  fi
  if command -v ethtool &>/dev/null; then
    mbits="$(ethtool "$iface" 2>/dev/null | awk -F': ' '/Speed:/ {gsub(/Mb.*/,"",$2); print $2}')"
    if [[ "$mbits" =~ ^[0-9]+$ ]] && [[ "$mbits" -ge 10 ]]; then
      echo "$mbits"
      return 0
    fi
  fi
  echo 100
}

preflight_capacity_estimate() {
  local iface="${1:-eth0}"
  local ram_mb cores lan_mbps raw min_est pessimistic

  ram_mb="$(preflight_read_mem_available_mb)"
  cores="$(preflight_read_cpu_cores)"
  lan_mbps="$(preflight_detect_lan_mbps "$iface")"

  local max_ram max_cpu max_bw
  max_ram=$(awk -v r="$ram_mb" -v ratio="$CAP_RAM_USABLE_RATIO" -v per="$CAP_MB_PER_CAMERA" \
    'BEGIN{printf "%d", int(r*ratio/per)}')
  max_cpu=$(awk -v c="$cores" -v ratio="$CAP_CPU_USABLE_RATIO" -v per="$CAP_CPU_SHARE_PER_CAMERA" \
    'BEGIN{printf "%d", int(c*ratio/per)}')
  max_bw=$(awk -v b="$lan_mbps" -v ratio="$CAP_BW_USABLE_RATIO" -v per="$CAP_MBPS_LAN_PER_CAMERA" \
    'BEGIN{printf "%d", int(b*ratio/per)}')

  raw="$max_ram"
  [[ "$max_cpu" -lt "$raw" ]] && raw="$max_cpu"
  [[ "$max_bw" -lt "$raw" ]] && raw="$max_bw"

  pessimistic=$(awk -v r="$raw" -v s="$CAP_SAFETY_FACTOR" 'BEGIN{v=int(r*s); if(v<1)v=1; print v}')
  if [[ "$raw" -gt 0 ]] && [[ "$pessimistic" -ge "$raw" ]]; then
    pessimistic=$((raw > 1 ? raw - 1 : 1))
  fi

  ui_section "Hardware y capacidad estimada (pesimista)"
  ui_detail "RAM disponible: ${ram_mb} MiB"
  ui_detail "Núcleos CPU: ${cores}"
  ui_detail "LAN ${iface}: ~${lan_mbps} Mbps (capacidad útil ~$(awk -v b="$lan_mbps" -v r="$CAP_BW_USABLE_RATIO" 'BEGIN{printf "%.0f", b*r}') Mbps)"
  echo ""
  ui_detail "Límites teóricos por recurso → RAM: ${max_ram} | CPU: ${max_cpu} | red: ${max_bw} cámaras"
  echo ""
  echo -e "  ${UI_BOLD}Recomendación pesimista:${UI_NC} ${UI_GREEN}${pessimistic}${UI_NC} cámaras en rebroadcast/ingesta simultánea"
  ui_warn "Cifra conservadora (ingest + relay copy, 720p@20fps). Si va holgado, puedes subir gradualmente."
  ui_detail "El búfer RAM (~60 s/cámara) y WebRTC/transcode consumen más; prueba en el panel antes de producción."

  PREFLIGHT_CAPACITY_PESSIMISTIC="$pessimistic"
  PREFLIGHT_CAPACITY_RAW="$raw"
}
