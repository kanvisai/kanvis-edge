# Matriz de brecha — Kanvis Edge (Fase 0.1)

Estado respecto al **roadmap acordado** (hardware autónomo + web de configuración + passthrough + playback).

Leyenda: ✅ Hecho · 🟡 Parcial · ❌ Pendiente · 🔄 Cambiar/refactor

| Capacidad | Estado | Notas |
|-----------|--------|-------|
| Ingesta RTSP LAN (codec copy, sin RGB) | ✅ | `StreamConsumer` + PyAV |
| Búfer RAM por tiempo (hasta 60 s configurable) | 🟡→✅ | Fase 0.3: por `monotonic`, no solo `maxlen` |
| Playback “ahora − X s” sin pedir a la cámara | 🟡→✅ | Fase 0.3: `GET /playback` |
| Clip evento nube (pasado + post en vivo) | ✅ | `GET /stream/{id}`; pasado vía snapshot |
| Config Twelve-Factor (.env + yaml) | ✅ | `config_loader.py` |
| Modelo cámara unificado (entrada/salida/búfer) | 🟡→✅ | Fase 0.2: `source` / `output` / `buffer` |
| Inventario JSON / SQLite CRUD | ✅ | Compatible con esquema anidado (SQLite + `config_json`) |
| Descubrimiento RTSP / ONVIF | ✅ | Opcional `DISCOVERY_ENABLED` |
| Auth API Key / JWT | ✅ | Middleware |
| DDNS DuckDNS / No-IP | ✅ | Bucle 5 min |
| Reporte IP a nube (webhook) | ✅ | `CLOUD_REPORT_URL` + POST JSON |
| Matriz port forwarding instalador | ✅ | `docs/PORT_FORWARDING.md` |
| RTSP passthrough / rebroadcast | ✅ | FFmpeg listen/push por cámara (`src/relay/`) |
| I-frame ~3 s en salida relay | 🟡 | `force_transcode_gop` + `iframe_interval_sec`; copy por defecto |
| WebRTC saliente | ✅ | WHEP (`/webrtc/{id}/offer`) + WHIP (`/webrtc/{id}/whip`) |
| Rewind 3 s en WebRTC | ✅ | `POST /webrtc/{id}/rewind?offset_sec=3` |
| Snapshots origen / relay | ✅ | `GET .../snapshot/source|relay` (JPEG) |
| Broadcast start/stop (UI prueba) | ✅ | `POST .../broadcast/start|stop` |
| Test playback documentado | ✅ | `POST .../test/playback` + `GET .../test/playback/stream` |
| UI web configuración (AP 192.192.192.192) | 🟡 | UI en `http://IP:8000/` lista; AP en Fase 5 |
| Punto de acceso `kanvis-XXXX` | ✅ | `scripts/kanvis-network.sh` + hostapd |
| systemd arranque automático | ✅ | `deploy/systemd/*.service` |
| Perfiles ap_only / lan_only / ap_and_lan | ✅ | `NETWORK_MODE` en `/etc/kanvis-edge/env` |
| Hot-reload relay por cámara | ✅ | Watcher 30 s reinicia relay si cambia config |
| RTSP gateway unificado (MediaMTX, opcional) | ✅ | Fase 7: un puerto + `/cam-id`; modo `direct` sin proxy |
| Métricas ingesta (packets/s, connected) | ✅ | `IngestMetrics` en `/cameras/{id}/status` |

## Cambios introducidos en Fase 1

1. **1.1** — Métricas de ingesta en `StreamConsumer` (`ingest` en status).
2. **1.2** — `RelayManager` + FFmpeg: modo **listen** (`rtsp://edge:PORT/path`) o **push** (`push_url`).
3. **1.3** — GOP ~3 s con `force_transcode_gop: true` (transcode H.264); por defecto codec copy.
4. **1.5** — Hot-reload: cambios en `cameras.json` reinician el subproceso relay.

## Cambios introducidos en Fase 0

1. **0.2** — `config.yaml` ampliado; `cameras.json` con `source`, `output`, `buffer`; migración automática desde formato plano legacy.
2. **0.3** — `BUFFER_DURATION_SECONDS` (default 60); API `GET /api/v1/playback/{camera_id}?offset_sec=&duration_sec=`; búfer recortado por tiempo real.

## Cambios introducidos en Fase 2

1. **2.1** — `aiortc` + `H264PacketVideoTrack` alimentado desde `PacketBridge` (ingesta sin RGB en búfer).
2. **2.2** — Modo **whep** (visor: POST offer → answer) y **whip** (push a `signaling_url`).
3. Rewind en sesión WebRTC vía API (usa búfer 60 s).

## Cambios introducidos en Fase 3

1. **3.1–3.2** — Snapshots JPEG vía FFmpeg (`/snapshot/source`, `/snapshot/relay`).
2. **3.3** — Broadcast (`/broadcast/start|stop|status`) y test playback.
3. **3.4** — CRUD/discovery ya existían; rutas alineadas para UI Fase 4.

## Cambios introducidos en Fase 4

1. **4.1** — Login local `WEBUI_USERNAME` / `WEBUI_PASSWORD` + JWT de sesión.
2. **4.2** — Panel en `/`: CRUD cámaras, relay, WebRTC, búfer.
3. **4.3** — Pestaña Probar: snapshots, broadcast, playback, estado.
4. **4.4** — Pestaña Sistema: resumen `/api/v1/config`.

## Cambios introducidos en Fase 5

1. **5.1** — `kanvis-edge.service` + usuario `kanvis`.
2. **5.2** — AP `kanvis-{device_id}` @ `192.168.192.192`, DHCP dnsmasq.
3. **5.3** — `NETWORK_MODE`: `ap_only`, `lan_only`, `ap_and_lan`.
4. **5.4** — `scripts/install.sh`, Docker documentado en `INSTALACION_HARDWARE.md`.

## Cambios introducidos en Fase 6

1. **6.1** — `WanSyncService` unifica DDNS (DuckDNS, No-IP, custom URL).
2. **6.2** — Reporte POST a `CLOUD_REPORT_URL` con `device_id`, IP, FQDN DDNS.
3. **6.3** — `docs/PORT_FORWARDING.md` + API `/connectivity/status|sync`.

## Cambios introducidos en Fase 7

1. **7.1** — `GatewayManager` + MediaMTX: rutas por `camera_id`, pull on-demand desde LAN.
2. **7.2** — `access_mode`: `direct` | `gateway` | `relay` (PF directo a cámara sigue disponible).
3. **7.3** — API `/gateway/status|reload`, reporte nube con `rtsp_gateway_wan_port`, `docs/RTSP_GATEWAY.md`.

## Roadmap completado (MVP hardware)

Fases 0–7 implementadas. Siguiente: endurecimiento producción (HTTPS, métricas, tests E2E con cámara real).
