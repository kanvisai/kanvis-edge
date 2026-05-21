# ¿Instalación nativa o Docker?

Son **dos formas alternativas** de ejecutar Kanvis Edge. **No** hace falta hacer las dos en el mismo cacharro para el mismo servicio.

## Comparación

| | **Nativa (`install.sh`)** | **Docker (`docker compose`)** |
|---|---------------------------|----------------------------------|
| **Qué instala** | Python en `/opt/kanvis-edge`, systemd | Contenedor con la app |
| **AP WiFi `kanvis-XXXX`** | Sí (`kanvis-network.service`) | **No** |
| **Panel `192.168.192.192`** | Sí (modo instalación) | Solo si accedes por IP LAN del host |
| **Arranque al boot** | `systemctl enable kanvis-edge` | `docker compose` + política restart |
| **Ideal para** | Raspberry / N100 / Jetson en tienda | Servidor con Docker, pruebas dev, sin AP |

## Recomendación por escenario

### Cacharro en la tienda (tu caso habitual)

```bash
sudo ./scripts/install.sh
sudo systemctl start kanvis-network kanvis-edge
```

**No** levantes Docker después para el gateway: duplicarías el puerto 8000 y el AP no lo configura Docker.

### Solo desarrollo en PC / servidor

```bash
docker compose up -d --build
```

Configura cámaras por volumen `./config` y abre `http://localhost:8000/`.

### Jetson con Docker por política corporativa

Puedes usar Docker **sin AP** (`NETWORK_MODE=lan_only` en el host) y configurar por IP Ethernet del Jetson. El modo “conectar al WiFi del cacharro” requiere instalación **nativa** del script de red.

## Resumen en una frase

**`install.sh` = producto en hardware con WiFi de instalación.**  
**`docker compose` = app empaquetada, sin AP automático.**
