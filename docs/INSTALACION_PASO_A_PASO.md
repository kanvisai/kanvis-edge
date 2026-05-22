# Instalación paso a paso (hardware)

Guía completa para desplegar **Kanvis Edge** en un cacharro Linux en tienda (Debian, Raspberry Pi, N100, etc.) con instalación **nativa** (`systemd`, sin Docker en producción).

> Resumen de arquitectura y Docker alternativo: [`DESPLIEGUE.md`](DESPLIEGUE.md)  
> API nube (reporte IP): [`BACKEND_CLOUD_API.md`](BACKEND_CLOUD_API.md)  
> Port forwarding: [`PORT_FORWARDING.md`](PORT_FORWARDING.md)

---

## Requisitos

| Requisito | Detalle |
|-----------|---------|
| SO | Debian 12, Raspberry Pi OS (Bookworm) o Ubuntu Server reciente |
| Python | 3.11+ (lo instala `preflight --install`) |
| Red | **Ethernet** al router de tienda (recomendado para operación y SSH) |
| WiFi | Interfaz con modo AP (`iw list` → *Supported interface modes* incluye **AP**) |
| Acceso | Teclado/monitor la primera vez, o SSH tras configurar red |
| Repo | `git clone` del proyecto; trabajarás desde la carpeta `kanvis-edge` |

---

## Mapa de ficheros: qué editar y cuándo

### Un solo fichero de entorno en el cacharro

En hardware **solo debes mantener** un fichero de variables:

| Ruta | Rol |
|------|-----|
| **`/etc/kanvis-edge/env`** | Configuración del sistema (red, claves, nube, usuario OS) |
| `/opt/kanvis-edge/.env` | **Enlace simbólico** → `/etc/kanvis-edge/env` (creado por `install.sh`) |

Comprueba:

```bash
ls -l /opt/kanvis-edge/.env
# Debe apuntar a /etc/kanvis-edge/env
```

No copies valores distintos en dos sitios.

### Cuándo editar cada cosa

| Momento | Fichero | Dónde |
|---------|---------|--------|
| **Opcional, antes de `install`** | Plantilla en el clone | `deploy/kanvis-edge.env.example` → sobre todo `KANVIS_OS_PASSWORD` |
| **Obligatorio, antes de `deploy`** | Entorno del sistema | `sudo nano /etc/kanvis-edge/env` |
| **Tras el primer arranque** | Cámaras | Panel web → Cámaras, o `config/cameras.json` |
| **Tras el primer arranque** | Horario búfer/broadcast | Panel web → Sistema, o `config/operating_schedule.json` |
| **Opcional** | YAML global | `config/config.yaml` (valores por defecto; el `.env` tiene prioridad si defines la misma variable allí) |

### Ficheros que genera la instalación

| Ruta | Contenido |
|------|-----------|
| `/opt/kanvis-edge/` | Código, `.venv`, scripts, `config/` |
| `/etc/systemd/system/kanvis-edge.service` | API + panel web `:8000` |
| `/etc/systemd/system/kanvis-network.service` | AP / red (`kanvis-network.sh`) |
| `/etc/systemd/system/kanvis-vnc.service` | TigerVNC (Debian/N100; opcional) |
| `/etc/ssh/sshd_config.d/99-kanvis-edge.conf` | Reenvío X11 para SSH |
| `config/cameras.json` | Inventario de cámaras |
| `config/operating_schedule.json` | Horario operativo (si lo guardas desde el panel) |

---

## Flujo recomendado (resumen)

```text
git clone → preflight → install → editar /etc/kanvis-edge/env → deploy
         → Ethernet + SSH → panel web (cámaras, horario, red)
```

| Paso | Comando | Crea / modifica |
|------|---------|-----------------|
| 0 | `preflight.sh --fix-services` | Solo si hubo conflicto puerto 53 |
| 1 | `preflight.sh` + `preflight.sh --install` | Paquetes APT (no borrar en uninstall) |
| 2 | `install.sh` | `/opt/kanvis-edge`, usuario `kanvis`, systemd, `/etc/kanvis-edge/env` |
| 3 | Editar env | Contraseñas, red, nube, interfaces WiFi/eth |
| 4 | `deploy.sh` | Arranca servicios, auditoría de placeholders |
| 5 | Panel + SSH | Cámaras, horario, pruebas |

---

## Reinstalación limpia (`uninstall.sh`)

Si ya instalaste y quieres empezar de cero **sin perder SSH**:

```bash
cd kanvis-edge   # clone en el cacharro, o copia solo scripts/uninstall.sh
sudo ./scripts/uninstall.sh
# o sin preguntar:
sudo ./scripts/uninstall.sh --yes
```

| Qué hace | Qué no hace |
|----------|-------------|
| Borra `/opt/kanvis-edge`, `/etc/kanvis-edge`, servicios systemd | No desinstala paquetes APT (`ffmpeg`, `hostapd`, …) |
| Para AP, restaura WiFi en NetworkManager | Por defecto **conserva** usuario `kanvis` y SSH |
| Mueve home de `kanvis` a `/home/kanvis` | |

Opciones:

```bash
sudo ./scripts/uninstall.sh --remove-user   # elimina también el usuario kanvis
sudo ./scripts/uninstall.sh --remove-ssh    # quita 99-kanvis-edge.conf
```

Después repite: **preflight → install → editar env → deploy**.

---

## Paso 0 — Conflicto dnsmasq / puerto 53 (solo si hace falta)

Al instalar el paquete `dnsmasq`, Debian a veces levanta `dnsmasq.service` y ocupa el puerto 53. El preflight lo evita; si ya falló:

```bash
cd kanvis-edge
sudo ./scripts/preflight.sh --fix-services
```

Kanvis usa su propio `dnsmasq` solo para DHCP del AP (`port=0`, sin DNS en :53).

---

## Paso 1 — Preflight (dependencias)

```bash
cd kanvis-edge
./scripts/preflight.sh
sudo ./scripts/preflight.sh --install
```

- Lista dependencias con `[OK]` / `[FALTA]`.
- `--install` instala lo que falte vía `apt`.
- **No** crea usuario `kanvis`, **no** levanta el AP ni el gateway.

---

## Paso 2 — Contraseña del usuario `kanvis` (recomendado antes de `install`)

Puedes definirla en el **clone** (se copia a `/etc/kanvis-edge/env` la primera vez que no exista ese fichero):

```bash
nano deploy/kanvis-edge.env.example
```

```env
KANVIS_OS_PASSWORD=TuContraseñaSegura123
```

Reglas PAM en Debian:

- **Sin** la palabra `kanvis` (ni variantes): PAM rechaza contraseñas que “contengan el nombre de usuario”.
- Evita `:` en la contraseña si usas herramientas que parsean mal.

Si no defines nada, `install.sh` genera una temporal y la muestra en pantalla — **cópiala**.

---

## Paso 3 — Instalar (`install.sh`)

```bash
sudo ./scripts/install.sh
```

Qué hace:

1. Comprueba/instala dependencias si faltan.
2. Copia el proyecto a **`/opt/kanvis-edge`** (rsync desde el clone).
3. Crea **`/etc/kanvis-edge/env`** desde la plantilla si no existe.
4. Enlaza **`/opt/kanvis-edge/.env`** → `/etc/kanvis-edge/env`.
5. Crea usuario **`kanvis`** (sudo), SSH (+X), VNC (TigerVNC o raspi-config en Pi).
6. Crea `.venv` e instala `requirements.txt`.
7. Opcional: descarga MediaMTX en `bin/mediamtx`.
8. Instala y **habilita** `kanvis-network.service` y `kanvis-edge.service` (no los arranca aún).

**No ejecuta `deploy`**: el gateway no corre hasta el paso 5.

Si falló a medias: corrige `KANVIS_OS_PASSWORD` en `/etc/kanvis-edge/env` o en la plantilla del clone y vuelve a lanzar `install.sh`.

---

## Paso 4 — Configuración (ANTES de `deploy.sh`)

```bash
sudo nano /etc/kanvis-edge/env
```

### Variables imprescindibles (sustituir placeholders)

| Variable | Uso |
|----------|-----|
| `KANVIS_OS_PASSWORD` | Login SSH/VNC del usuario `kanvis` |
| `WEBUI_USERNAME` / `WEBUI_PASSWORD` | Panel web `:8000` |
| `JWT_SECRET` | Tokens API / webui |
| `API_KEY` | Cliente nube / integraciones |
| `DEVICE_NAME` | Nombre legible en Kanvis C4 |
| `DEVICE_ID` | Sufijo WiFi `kanvis-<DEVICE_ID>` |
| `WLAN_INTERFACE` | Interfaz WiFi real (`ip link`; ej. `wlP1p1s0`, `wlan0`) |
| `LAN_INTERFACE` | Ethernet (ej. `eth0`) |

### Red — elige el modo según tu caso

| `NETWORK_MODE` | Cuándo usarlo |
|----------------|---------------|
| **`lan_only`** | Cacharro en casa/oficina: solo Ethernet o WiFi cliente; **sin AP**. Ideal para configurar por SSH sin perder internet. |
| **`ap_and_lan`** | Tienda: **cable al router** (internet + SSH por LAN) y **WiFi como AP** `kanvis-XXXX` para el móvil del instalador. |
| **`ap_only`** | Sin router: solo AP en el WiFi (el cacharro **no** tendrá internet por esa interfaz). |

Ejemplo recomendado **en tienda con cable**:

```env
NETWORK_MODE=ap_and_lan
LAN_INTERFACE=eth0
WLAN_INTERFACE=wlP1p1s0
AP_SSID_PREFIX=kanvis
AP_IP=192.168.192.192
AP_PASSWORD=kanvis-install
```

Ejemplo **solo SSH por Ethernet** (sin AP al arrancar):

```env
NETWORK_MODE=lan_only
```

> **Error frecuente:** `NETWORK_MODE=ap_only` o AP en la **única** interfaz WiFi sin cable → pierdes SSH e internet por WiFi. Recuperación: consola local, `kanvis-network.sh stop`, `nmcli` a tu WiFi, o conéctate al AP `kanvis-XXXX` en `192.168.192.192`.

### Nube (opcional)

```env
CLOUD_REPORT_ENABLED=true
CLOUD_REPORT_URL=http://TU-BACKEND:7777/api/v1/kanvis-edges/report-public-ip
CLOUD_ACCESS_TOKEN=token-del-alta-en-c4
```

`CLOUD_ACCESS_TOKEN` ≠ `DDNS_TOKEN`. Detalle: [`BACKEND_CLOUD_API.md`](BACKEND_CLOUD_API.md).

### Plantilla completa

Ver `deploy/kanvis-edge.env.example` en el repositorio.

---

## Paso 5 — Desplegar y arrancar (`deploy.sh`)

```bash
sudo /opt/kanvis-edge/scripts/deploy.sh
```

1. **Pausa** — pulsa ENTER cuando hayas guardado `/etc/kanvis-edge/env`.
2. **Auditoría** — avisa si quedan `change-me`, `replace-with`, etc.
3. Opcional: si ejecutas desde el clone, **rsync** actualiza código en `/opt/kanvis-edge`.
4. Arranca **`kanvis-network`** y **`kanvis-edge`**.

Sin pausa (CI o ya revisado):

```bash
sudo /opt/kanvis-edge/scripts/deploy.sh --yes
```

Comprobaciones:

```bash
sudo systemctl status kanvis-edge kanvis-network
curl -s http://127.0.0.1:8000/api/v1/health
journalctl -u kanvis-edge -n 30 --no-pager
```

---

## Paso 6 — Acceso al panel y configuración funcional

### A) Por Ethernet + SSH (recomendado en producción)

1. Conecta **cable** al router de la tienda.
2. Obtén IP: `hostname -I` o router DHCP.
3. Desde tu PC en la misma red:

```bash
ssh kanvis@<IP_ETH>
```

4. Panel: **`http://<IP_ETH>:8000/`**  
5. Login: `WEBUI_USERNAME` / `WEBUI_PASSWORD`.

Si usas `ap_and_lan`, el AP puede coexistir; el acceso estable es por **IP de Ethernet**.

### B) Por WiFi de instalación (AP)

1. Conecta el PC/móvil al WiFi **`kanvis-<DEVICE_ID>`** (contraseña `AP_PASSWORD`).
2. Abre **`http://192.168.192.192:8000/`** (o `AP_IP` configurado).

### En el panel web

| Pestaña | Qué configurar |
|---------|----------------|
| **Cámaras** | Alta/edición: marca Annke, IP, canal 101, relay, búfer |
| **Probar** | Snapshot, broadcast, playback, WebRTC |
| **Sistema** | Horario operativo, sync IP nube, info dispositivo |

---

## Horario operativo (búfer + broadcast)

En **Sistema → Horario operativo** puedes activar varias franjas (ej. lun–sáb 08:50–14:05 y 16:55–21:05). Fuera de horario:

- No hay ingesta RTSP ni búfer en RAM.
- No hay rebroadcast RTSP (relay).

Se guarda en **`config/operating_schedule.json`**. Ejemplo en `config/operating_schedule.example.json`.

API: `GET/PUT /api/v1/operating-schedule`

---

## Inventario de cámaras

| Método | Fichero |
|--------|---------|
| Panel web | Pestaña Cámaras |
| Manual | `/opt/kanvis-edge/config/cameras.json` |
| Esquema | `config/cameras.schema.json` |

Tras cambiar JSON a mano, el servicio recarga en ~30 s o reinicia:

```bash
sudo systemctl restart kanvis-edge
```

---

## Scripts de referencia

| Script | Uso |
|--------|-----|
| `scripts/preflight.sh` | Comprobar dependencias |
| `scripts/preflight.sh --install` | Instalar paquetes APT |
| `scripts/preflight.sh --fix-services` | Arreglar dnsmasq/hostapd del sistema |
| `scripts/install.sh` | Instalación nativa completa |
| `scripts/deploy.sh` | Auditoría + arranque servicios |
| `scripts/uninstall.sh` | Desinstalar ficheros y servicios Kanvis |
| `scripts/kanvis-network.sh start\|stop\|status` | AP manual (debug) |

---

## Servicios systemd

| Unidad | Función |
|--------|---------|
| `kanvis-network.service` | Red AP/LAN (`ExecStart` → `kanvis-network.sh start`) |
| `kanvis-edge.service` | Gateway Python (API + UI en `EDGE_API_PORT`) |
| `kanvis-vnc.service` | VNC TigerVNC (si está habilitado) |

Comandos útiles:

```bash
sudo systemctl restart kanvis-edge
sudo systemctl stop kanvis-network
sudo systemctl disable kanvis-network    # si pasas a lan_only permanente
sudo journalctl -u kanvis-edge -f
sudo /opt/kanvis-edge/scripts/kanvis-network.sh status
```

---

## Panel no carga en `http://192.168.192.192:8000`

1. **URL:** `http://` (no `https://`), puerto **`:8000`**.
2. **Servicio local** (en el cacharro, por SSH o consola):

```bash
sudo systemctl status kanvis-edge
curl -s http://127.0.0.1:8000/api/v1/health
ss -tlnp | grep 8000
```

Debe salir `{"status":"ok"}` y algo escuchando en `0.0.0.0:8000` (no solo `127.0.0.1`).

3. **`EDGE_API_HOST`** en `/etc/kanvis-edge/env`:

```env
EDGE_API_HOST=0.0.0.0
EDGE_API_PORT=8000
```

Si es `127.0.0.1`, el panel solo funciona en el propio cacharro, no desde el móvil en el AP.

4. **systemd** (fallos por hardening): actualiza la unidad y recarga:

```bash
sudo cp /opt/kanvis-edge/deploy/systemd/kanvis-edge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart kanvis-edge
```

5. **Logs de arranque:**

```bash
journalctl -u kanvis-edge -n 50 --no-pager
```

6. El mensaje de deploy **«API no responde»** se refiere al gateway en **este** equipo, no a `CLOUD_REPORT_URL`. El reporte a nube puede fallar aparte si el backend remoto no está accesible.

---

## Solución de problemas

| Problema | Qué hacer |
|----------|-----------|
| Preflight: puerto 53 en uso | `sudo ./scripts/preflight.sh --fix-services` |
| `install`: missing new password | `KANVIS_OS_PASSWORD=...` en env o plantilla; re-ejecutar `install.sh` |
| `install`: BAD PASSWORD contains user name | Contraseña sin substring `kanvis`; o `sudo usermod -p "$(openssl passwd -6 '...')" kanvis` |
| Perdí SSH tras levantar AP | Consola: `kanvis-network.sh stop`; `nmcli`; o AP → `ssh kanvis@192.168.192.192` |
| `WLAN_INTERFACE` incorrecta | `ip link` → poner nombre real en `/etc/kanvis-edge/env` |
| Panel no abre / móvil en AP sin respuesta | Ver sección **Panel no carga** abajo |
| Deploy: «La API no responde» | Es la API **local** (`kanvis-edge`), no el backend nube. `journalctl -u kanvis-edge -n 50` |
| Sin WiFi `kanvis-XXXX` | `iw list` (modo AP); `journalctl -u kanvis-network` |
| `.env` y `/etc/.../env` distintos | Un solo fichero: edita `/etc/kanvis-edge/env`; verifica el enlace en `/opt` |
| Línea suelta en env → `command not found` | Cada línea debe ser `CLAVE=valor` (tokens sin `CLOUD_ACCESS_TOKEN=` rompen scripts) |
| Cámara “Fuera de horario” | Normal si el horario operativo está activo y fuera de franja |
| Reinstalar desde cero | `uninstall.sh` → `install.sh` → editar env → `deploy.sh` |

---

## Orden correcto si algo falló

```text
preflight [--install] → install → /etc/kanvis-edge/env → deploy
```

No ejecutes `kanvis-network.sh start` a mano hasta entender `NETWORK_MODE` e interfaces. Para desarrollo en casa: **`lan_only`** + Ethernet o WiFi cliente, y configura el AP más tarde con cable conectado (`ap_and_lan`).

---

## Documentación relacionada

- [`INSTALACION_HARDWARE.md`](INSTALACION_HARDWARE.md) — Resumen hardware, modos red, Docker
- [`RTSP_GATEWAY.md`](RTSP_GATEWAY.md) — MediaMTX y un solo puerto WAN
- [`README.md`](../README.md) — API REST, búfer, relay
