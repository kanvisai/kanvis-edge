# RTSP Gateway unificado (Fase 7)

Proxy RTSP **opcional** en el Kanvis Edge: **un solo puerto** y rutas por cámara (`/cam-01`, …). Alternativa al port forwarding **por cámara** hacia la IP de cada cámara.

## Tres modos de acceso externo

| Modo | Config | Router tienda | URL ejemplo |
|------|--------|---------------|-------------|
| **direct** | `gateway.enabled=false`, `access_mode=direct` | WAN:55400 → **cámara** :554 | `rtsp://tienda1.kanvis.ai:55400/Streaming/Channels/101` |
| **gateway** | `RTSP_GATEWAY_ENABLED=true`, `gateway.enabled=true` | WAN:55422 → **edge** :8554 | `rtsp://tienda1.kanvis.ai:55422/cam-01` |
| **relay** | `output.relay.enabled=true` | WAN:55423 → edge:8555… | `rtsp://tienda1.kanvis.ai:55423/cam-01` (FFmpeg) |

Puedes mezclar modos por cámara (p. ej. una en **direct** para comparar latencia y el resto en **gateway**).

## Activar el gateway

### 1. Global (`.env` o `config.yaml`)

```env
RTSP_GATEWAY_ENABLED=true
RTSP_GATEWAY_PORT=8554
RTSP_GATEWAY_WAN_PORT=55422
MEDIAMTX_BINARY=mediamtx
```

```yaml
rtsp_gateway:
  rtsp_gateway_enabled: true
  rtsp_gateway_port: 8554
  rtsp_gateway_wan_port: 55422
```

### 2. Por cámara (`cameras.json`)

```json
"output": {
  "protocol": "rtsp",
  "gateway": {
    "enabled": true,
    "access_mode": "gateway",
    "path": "cam-01",
    "username": "",
    "password": "",
    "source_on_demand": true
  },
  "relay": { "enabled": false }
}
```

### 3. Router

Una sola regla: `WAN:55422` → `IP_EDGE:8554/TCP`

DDNS (`tienda1.kanvis.ai`) sigue apuntando a la IP pública del router; el edge reporta puertos en el webhook de nube (`rtsp_gateway_wan_port`).

### 4. MediaMTX

El instalador intenta descargar el binario a `/opt/kanvis-edge/bin/mediamtx`. También puedes instalarlo a mano y poner `MEDIAMTX_BINARY=/usr/local/bin/mediamtx`.

## API

```bash
curl -H "X-API-Key: KEY" http://127.0.0.1:8000/api/v1/gateway/status
curl -H "X-API-Key: KEY" -X POST http://127.0.0.1:8000/api/v1/gateway/reload
```

`GET /api/v1/cameras/{id}/status` incluye `gateway` con `url_local` y `access_mode`.

## Comparar latencia (A/B)

1. Cámara A: `access_mode=direct` + PF al puerto de la cámara.
2. Cámara B: `gateway.enabled=true` + un solo PF al edge.
3. Mide en VLC estadísticas de red o contadores de frames perdidos.

Si el proxy no convence, desactiva `RTSP_GATEWAY_ENABLED` y deja **direct** o **relay** sin tocar el búfer de eventos ni la ingesta.

## Notas

- El path externo es `/{camera_id}` (o `gateway.path`), **no** el path Hikvision completo; MediaMTX tira del origen configurado en LAN.
- `source_on_demand: true` (por defecto) abre el pull a la cámara solo cuando hay un cliente — ahorra ancho de banda.
- No uses el mismo puerto para gateway y relay listen sin offset; por defecto relay usa `8554+N` si la cámara no define `listen_port`.

Ver también: [PORT_FORWARDING.md](PORT_FORWARDING.md)
