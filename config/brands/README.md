# Perfiles de marca (RTSP)

Un fichero JSON por fabricante: `<slug>.json` (ej. `annke.json`).

Al registrar una cámara en el panel, elige **Marca** y el edge construye:

- **Vivo** → plantilla `stream_template` hacia la IP de la cámara
- **Playback** → plantilla `playback_template` (con `starttime` / `endtime`)
- **Acceso externo vía gateway** → misma ruta que el fabricante (`Streaming/channels/101`) pero apuntando al host/puerto del edge

Añade nuevas marcas copiando `annke.json` y ajustando las plantillas. Ver esquema en el README de kanvis-monitoring `config/brands/`.

### TP-Link / Tapo

- **Vivo:** `rtsp://…/stream1` (o `stream2`).
- **Playback en la cámara:** las Tapo **no** suelen exponer grabación RTSP en el propio equipo; una URL tipo `/Streaming/tracks/…` devuelve **404** si apuntas a la IP de la cámara.
- **Playback con búfer Kanvis:** el cliente debe pedir al **edge** (host/puerto del gateway MediaMTX), misma query `starttime`/`endtime`, ruta **inventada** en el gateway (p. ej. `/Streaming/tracks/101`). El edge sirve el tramo reciente desde RAM; no hace falta que la Tapo entienda playback RTSP.
- En `cameras.json` puedes usar `"channel": "stream1"` para vivo y `"playback_channel": "101"` para la URL de playback del edge (convención tipo Annke/Hik).
