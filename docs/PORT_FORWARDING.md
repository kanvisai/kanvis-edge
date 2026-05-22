# Matriz de port forwarding — instalador en tienda

Guía para abrir puertos en el **router de la tienda** hacia el **Kanvis Edge** (IP LAN del cacharro, p. ej. `192.168.1.50`).

## Resumen rápido

| Acceso desde internet | ¿Hace falta abrir puerto? | Alternativa sin abrir |
|------------------------|---------------------------|------------------------|
| API / playback / UI | **Sí** (o túnel/VPN) | Edge hace **push** saliente (WHIP, relay push) |
| RTSP relay en edge | **Sí** (puerto alto) | Mismo push RTSP |
| WebRTC | **Sí** (TCP + UDP ICE) | WHIP hacia tu servidor |

## Tabla de puertos (valores por defecto)

| Servicio | Puerto **interno** (edge) | Puerto **WAN** sugerido | Protocolo | Obligatorio |
|----------|---------------------------|-------------------------|-----------|-------------|
| API Gateway (REST, UI, playback) | `8000` (`EDGE_API_PORT`) | `55443` o alto aleatorio | TCP | Sí, si la nube **entra** al edge |
| RTSP gateway (MediaMTX, opcional) | `8554` (`RTSP_GATEWAY_PORT`) | `55422` (`RTSP_GATEWAY_WAN_PORT`) | TCP | **Un puerto** para todas las cámaras con `gateway.enabled` |
| RTSP relay (listen) | `8554` + offset por cámara | `55423`, `55424`… | TCP | Alternativa por cámara (FFmpeg) |
| RTSP directo a cámara | `554` en cámara | `55400`, `55401`… | TCP | Sin edge; `access_mode=direct` |
| WebRTC signaling | `8188` | `55488` | TCP | Piloto WebRTC |
| WebRTC media (ICE) | dinámico | `50000-51000` | UDP | Piloto WebRTC |

**Regla en el router:** `WAN:55443` → `IP_EDGE:8000/TCP`

## DDNS + nube Kanvis (Fase 6)

1. En el edge (`.env`):
   ```env
   DDNS_ENABLED=true
   DDNS_PROVIDER=duckdns
   DDNS_HOSTNAME=mi-tienda-001
   DDNS_TOKEN=...
   DEVICE_ID=tienda-001

   CLOUD_REPORT_ENABLED=true
   CLOUD_REPORT_URL=http://kanvis-pilot.ai:7777/api/v1/kanvis-edges/report-public-ip
   DEVICE_NAME=store-01-edge
   CLOUD_ACCESS_TOKEN=...
   ```

2. El edge envía (sin Bearer):
   ```json
   {
     "device_name": "store-01-edge",
     "access_token": "...",
     "public_ip": "203.0.113.10"
   }
   ```
   Ver [`BACKEND_CLOUD_API.md`](BACKEND_CLOUD_API.md).

3. El backend guarda la IP y puedes construir URLs (DDNS opcional en el edge):
   - API: `http://mi-tienda-001.duckdns.org:55443/api/v1/...`
   - O directamente `http://203.0.113.10:55443/...` si el DDNS tarda en propagar

## Comprobación

```bash
# En el edge
curl -H "X-API-Key: KEY" http://127.0.0.1:8000/api/v1/connectivity/status
curl -H "X-API-Key: KEY" -X POST http://127.0.0.1:8000/api/v1/connectivity/sync
```

Desde fuera de la tienda (móvil sin WiFi tienda):

```bash
curl -H "X-API-Key: KEY" http://TU_DDNS:55443/api/v1/health
```

## RTSP gateway unificado (recomendado si abres RTSP)

Ver **`docs/RTSP_GATEWAY.md`**: `rtsp://tu-ddns:55422/cam-01` con una sola regla NAT hacia el edge. Si el proxy añade latencia, usa `access_mode=direct` en esa cámara y PF directo a su IP.

## Modo sin port forwarding (push)

- `output.relay.mode=push` → RTSP hacia servidor en nube
- `output.webrtc.mode=whip` → WebRTC hacia `signaling_url`
- El edge **no** necesita puertos entrantes; solo salida a internet

## Seguridad

- No expongas el puerto **554** estándar en WAN (escaneos masivos).
- Usa **API_KEY** fuerte y HTTPS delante del edge (reverse proxy o VPN) en producción.
- Cambia contraseñas `WEBUI_PASSWORD` y `AP_PASSWORD` tras la instalación.
