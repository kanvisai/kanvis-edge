# Instalación en hardware (Fase 5)

Guía para Raspberry Pi, Jetson Orin Nano, Intel N100 u otro Linux en la tienda.

> **¿Y Docker?** Son caminos distintos. Ver [`DESPLIEGUE.md`](DESPLIEGUE.md). En el dispositivo de tienda usa **`install.sh` + systemd**, no hace falta `docker compose` después.

## Requisitos

- Debian 12 / Raspberry Pi OS / Ubuntu Server
- WiFi con soporte AP (`wlan0` o similar) para modo instalación
- Ethernet (`eth0`) hacia el router de la tienda (modo `ap_and_lan`)
- Paquetes: `hostapd`, `dnsmasq`, `ffmpeg`, Python 3.11+

## Instalación en 3 pasos (recomendado)

```bash
cd kanvis-edge

# 1) Preflight: dependencias [OK]/[FALTA] + estimación pesimista de cámaras
./scripts/preflight.sh
sudo ./scripts/preflight.sh --install

# 2) Instalador visual (usuario kanvis, SSH, VNC, Python, systemd)
sudo ./scripts/install.sh

# 3) Pausa para editar config, auditoría y arranque
sudo ./scripts/deploy.sh
```

`deploy.sh` lista qué cambiar en `/opt/kanvis-edge/.env` y `/etc/kanvis-edge/env` antes de levantar servicios.

## Instalación rápida (manual)

```bash
sudo ./scripts/install.sh
sudo nano /opt/kanvis-edge/.env
sudo nano /etc/kanvis-edge/env
sudo ./scripts/deploy.sh
```

### Usuario `kanvis`, SSH y VNC

`install.sh` crea el usuario **`kanvis`** con contraseña **`KANVIS_OS_PASSWORD`** (en `/etc/kanvis-edge/env` o `/opt/kanvis-edge/.env`), grupo **`sudo`**, **OpenSSH** con reenvío **X11** (`ssh -X`) y **VNC**:

| Variable | Default |
|----------|---------|
| `KANVIS_OS_PASSWORD` | Obligatoria en producción (`change-me-on-install` genera una temporal en el log) |
| `KANVIS_ENABLE_SSH` | `true` |
| `KANVIS_ENABLE_VNC` | `true` |
| `KANVIS_VNC_DISPLAY` | `:1` (Debian/N100: servicio `kanvis-vnc`; Raspberry Pi OS: VNC de `raspi-config`) |

```bash
ssh -X kanvis@192.168.192.192    # apps gráficas con +X
# VNC: <IP>:5901 si display :1 (TigerVNC)
```

Tokens hacia la nube: [`BACKEND_CLOUD_API.md`](BACKEND_CLOUD_API.md).

## Modos de red (`/etc/kanvis-edge/env`)

| `NETWORK_MODE` | Comportamiento |
|----------------|----------------|
| `ap_only` | Solo WiFi AP `kanvis-XXXX` → configuración sin router |
| `lan_only` | Solo Ethernet DHCP → tienda normal, sin AP |
| `ap_and_lan` | AP + Ethernet (recomendado en instalación) |

### AP de instalación

| Variable | Default |
|----------|---------|
| `AP_SSID_PREFIX` | `kanvis` → SSID `kanvis-a1b2c3` |
| `AP_IP` | `192.168.192.192` |
| `AP_PASSWORD` | `kanvis-install` (vacío = AP abierto) |
| `WLAN_INTERFACE` | `wlan0` |

Tras arrancar:

1. Conecta el móvil/PC al WiFi **`kanvis-XXXX`**
2. Abre **`http://192.168.192.192:8000/`**
3. Login con `WEBUI_USERNAME` / `WEBUI_PASSWORD`

## Servicios systemd

| Unidad | Función |
|--------|---------|
| `kanvis-network.service` | Levanta AP/DHCP (oneshot) |
| `kanvis-edge.service` | Gateway Python (API + UI) |

```bash
sudo systemctl status kanvis-edge
sudo journalctl -u kanvis-edge -f
sudo ./opt/kanvis-edge/scripts/kanvis-network.sh status
```

## Producción en tienda

1. Conecta **Ethernet** al router de la tienda.
2. Opcional: cambia `NETWORK_MODE=lan_only` y deshabilita AP:
   ```bash
   sudo systemctl disable kanvis-network
   ```
3. Configura **port forwarding** en el router hacia el edge (API `8000` o puerto alto).
4. Ajusta `cameras.json` desde el panel o API.

## Docker (alternativa)

```bash
docker compose up -d --build
```

Docker **no** configura el AP; usa instalación nativa para el modo `kanvis-XXXX`.

### Multi-arquitectura

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t kanvis-edge:latest .
```

## Detección de distro (Raspberry Pi OS vs Debian)

`kanvis-network.sh` usa `scripts/lib/distro.sh`:

| Distro detectada | Acciones extra |
|------------------|----------------|
| **raspberry_pi_os** | Deshabilita hostapd/dnsmasq del sistema, `denyinterfaces wlan0` en dhcpcd, para wpa_supplicant en wlan, `nmcli managed no`, país WiFi con `raspi-config` |
| **debian** (N100, etc.) | Para servicios hostapd/dnsmasq del sistema, libera wlan de NetworkManager |

Comprueba con:

```bash
sudo /opt/kanvis-edge/scripts/kanvis-network.sh status
# DISTRO=raspberry_pi_os  o  debian
```

## Solución de problemas

| Problema | Acción |
|----------|--------|
| No aparece WiFi | `iw list` debe mostrar AP; revisa `WLAN_INTERFACE` |
| hostapd falla en Pi | `sudo systemctl start kanvis-network` y mira `journalctl -u kanvis-network`; el script ya aplica perfil RPi OS |
| No abre la web | `curl http://127.0.0.1:8000/api/v1/health` en el device |
| Sin DHCP en AP | `apt install dnsmasq`; revisa logs `journalctl -u kanvis-network` |
| dnsmasq: `port 53` en uso | El AP usa `port=0` (solo DHCP). Para: `sudo systemctl restart kanvis-network`. Si falla: `sudo systemctl stop dnsmasq` y `mask dnsmasq` |
