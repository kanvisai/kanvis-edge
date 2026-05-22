# Instalación paso a paso (hardware)

Asumes que ya hiciste `git clone` y estás en la carpeta `kanvis-edge`.

## Qué editar ANTES de cada comando

| Momento | Fichero único en hardware |
|---------|---------------------------|
| **Antes de `install` (opcional)** | `deploy/kanvis-edge.env.example` en el clone |
| **Antes de `deploy`** | **`/etc/kanvis-edge/env`** (y `/opt/kanvis-edge/.env` es el mismo enlace) |

No edites dos sitios con valores distintos: un solo fichero.

---

## Paso 0 — Arreglar dnsmasq (solo si el preflight falló en puerto 53)

```bash
cd kanvis-edge
sudo ./scripts/preflight.sh --fix-services
```

---

## Paso 1 — Preflight (dependencias)

```bash
./scripts/preflight.sh
sudo ./scripts/preflight.sh --install
```

No crea usuario ni red WiFi kanvis.

---

## Paso 2 — (Recomendado) Contraseña del usuario `kanvis` antes de instalar

Edita **en el clone** (se copiará a `/etc/kanvis-edge/env` la primera vez):

```bash
nano deploy/kanvis-edge.env.example
```

Cambia:

```env
KANVIS_OS_PASSWORD=TuContraseñaSegura123
```

- Sin `:` en la contraseña.
- **Sin la palabra `kanvis`** (ni Kanvis/KANVIS): Debian/PAM lo rechaza aunque no sea el usuario literal ("contains the user name in some form").
- Si no la pones, `install.sh` generará una aleatoria — **cópiala**.

---

## Paso 3 — Instalar (`install.sh`)

```bash
sudo ./scripts/install.sh
```

- Instala en `/opt/kanvis-edge`
- Crea usuario `kanvis` con la contraseña de arriba
- SSH, VNC, Python, systemd (**no arranca** el gateway aún)

Si falló antes a medias, vuelve a lanzar el mismo comando tras poner `KANVIS_OS_PASSWORD`.

---

## Paso 4 — Configuración (ANTES de `deploy.sh`)

```bash
sudo nano /etc/kanvis-edge/env
```

(`ls -l /opt/kanvis-edge/.env` debe apuntar al mismo fichero.)

### Variables principales (todo en `/etc/kanvis-edge/env`)

```env
DEVICE_NAME=store-01-edge          # igual que en Kanvis C4
DEVICE_ID=mi-tienda-01             # sufijo WiFi kanvis-XXXX
WEBUI_USERNAME=admin
WEBUI_PASSWORD=TuPasswordPanel
JWT_SECRET=una-cadena-larga-aleatoria
API_KEY=clave-para-nube-edge

CLOUD_REPORT_ENABLED=true
CLOUD_REPORT_URL=http://TU-BACKEND:7777/api/v1/kanvis-edges/report-public-ip
CLOUD_ACCESS_TOKEN=token-del-alta-c4
```

Incluye también `KANVIS_OS_PASSWORD`, `WLAN_INTERFACE`, `AP_PASSWORD`, `NETWORK_MODE`, etc.

---

## Paso 5 — Desplegar y arrancar (`deploy.sh`)

```bash
sudo /opt/kanvis-edge/scripts/deploy.sh
```

- Pausa → ENTER cuando hayas guardado los ficheros
- Arranca `kanvis-network` (WiFi AP) y `kanvis-edge` (API + panel)

---

## Paso 6 — Probar

1. WiFi **`kanvis-<DEVICE_ID>`** (contraseña `AP_PASSWORD`)
2. Navegador: **http://192.168.192.192:8000/**
3. Login panel: `WEBUI_USERNAME` / `WEBUI_PASSWORD`
4. Pestaña Cámaras → añadir cámara (marca Annke, IP, canal 101…)

---

## Si `install` dijo "missing new password"

1. Pon `KANVIS_OS_PASSWORD=AlgoSeguro` en `deploy/network/kanvis-edge.env.example` **o** en `/etc/kanvis-edge/env`
2. `sudo ./scripts/install.sh` otra vez

## Si `install` dijo "BAD PASSWORD: contains the user name"

Debian comprueba que la contraseña **no incluya `kanvis`** (a veces falla con `TiendaKanvis1`, `Kanvis2026`, etc.). Usa otra, por ejemplo `Edge-Tienda-2026!x`.

Arreglo manual (no pasa por PAM):

```bash
sudo usermod -p "$(openssl passwd -6 'Edge-Tienda-2026!x')" kanvis
```

Luego la misma clave en `/etc/kanvis-edge/env` → `KANVIS_OS_PASSWORD=...`
