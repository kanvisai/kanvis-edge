# Instalación en hardware

Guía corta para Raspberry Pi, Jetson Orin Nano, Intel N100 u otro Linux en tienda.

> **Guía detallada (pasos, ficheros a editar, troubleshooting):** [`INSTALACION_PASO_A_PASO.md`](INSTALACION_PASO_A_PASO.md)

> **¿Docker?** Camino distinto → [`DESPLIEGUE.md`](DESPLIEGUE.md). En tienda usa **`install.sh` + systemd**.

## Requisitos

- Debian 12 / Raspberry Pi OS / Ubuntu Server
- WiFi con soporte AP (`iw list`)
- Ethernet al router (recomendado para `ap_and_lan` e internet/SSH)
- Python 3.11+, `ffmpeg`, `hostapd`, `dnsmasq` (los instala preflight)

## Instalación en 4 comandos

```bash
cd kanvis-edge
./scripts/preflight.sh && sudo ./scripts/preflight.sh --install
sudo ./scripts/install.sh
sudo nano /etc/kanvis-edge/env    # ANTES de deploy
sudo /opt/kanvis-edge/scripts/deploy.sh
```

### Fichero de configuración

| En hardware | Contenido |
|-------------|-----------|
| **`/etc/kanvis-edge/env`** | Único fichero de variables (red, claves, nube) |
| `/opt/kanvis-edge/.env` | Enlace simbólico al anterior |

Opcional antes de `install`: editar `deploy/kanvis-edge.env.example` en el clone (`KANVIS_OS_PASSWORD`).

Tras arranque: cámaras y horario en el **panel** `:8000` o en `config/cameras.json` y `config/operating_schedule.json`.

## Desinstalación / reinstalación

```bash
sudo ./scripts/uninstall.sh --yes
```

Conserva usuario `kanvis` y SSH por defecto. Luego repite preflight → install → env → deploy.

## Modos de red

| `NETWORK_MODE` | Uso |
|----------------|-----|
| `lan_only` | Solo LAN; sin AP. Ideal para configurar por SSH en casa. |
| `ap_and_lan` | Ethernet = internet/SSH; WiFi = AP `kanvis-XXXX`. **Recomendado en tienda.** |
| `ap_only` | Solo AP; sin internet por WiFi si es la única interfaz. |

Variables: `WLAN_INTERFACE`, `LAN_INTERFACE`, `AP_PASSWORD`, `DEVICE_ID` en `/etc/kanvis-edge/env`.

## Servicios

| Unidad | Función |
|--------|---------|
| `kanvis-network.service` | AP / red |
| `kanvis-edge.service` | API + panel web |

```bash
sudo systemctl status kanvis-edge
sudo journalctl -u kanvis-edge -f
```

## Acceso

- SSH: `ssh kanvis@<IP>` (contraseña `KANVIS_OS_PASSWORD`)
- Panel: `http://<IP>:8000/` (`WEBUI_USERNAME` / `WEBUI_PASSWORD`)
- AP instalación: WiFi `kanvis-<DEVICE_ID>` → `http://192.168.192.192:8000/`

## Más documentación

- Paso a paso completo: [`INSTALACION_PASO_A_PASO.md`](INSTALACION_PASO_A_PASO.md)
- Nube: [`BACKEND_CLOUD_API.md`](BACKEND_CLOUD_API.md)
- RTSP gateway: [`RTSP_GATEWAY.md`](RTSP_GATEWAY.md)
