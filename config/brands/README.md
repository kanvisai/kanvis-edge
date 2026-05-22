# Perfiles de marca (RTSP)

Un fichero JSON por fabricante: `<slug>.json` (ej. `annke.json`).

Al registrar una cámara en el panel, elige **Marca** y el edge construye:

- **Vivo** → plantilla `stream_template` hacia la IP de la cámara
- **Playback** → plantilla `playback_template` (con `starttime` / `endtime`)
- **Acceso externo vía gateway** → misma ruta que el fabricante (`Streaming/channels/101`) pero apuntando al host/puerto del edge

Añade nuevas marcas copiando `annke.json` y ajustando las plantillas. Ver esquema en el README de kanvis-monitoring `config/brands/`.
