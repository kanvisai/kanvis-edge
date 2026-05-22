# API Kanvis — reporte de IP pública (edge)

Contrato del backend Kanvis para que el **edge** registre su IP WAN. Sin JWT de usuario: autenticación por `device_name` + `access_token` en el body.

## Configuración en el edge

```bash
sudo nano /opt/kanvis-edge/.env
```

```env
CLOUD_REPORT_ENABLED=true
CLOUD_REPORT_URL=http://kanvis-pilot.ai:7777/api/v1/kanvis-edges/report-public-ip
DEVICE_NAME=store-01-edge
CLOUD_ACCESS_TOKEN=token-asignado-en-c4-al-registrar-el-edge
CLOUD_REPORT_ON_IP_CHANGE_ONLY=true
WAN_SYNC_INTERVAL_SECONDS=300
```

| Variable | Uso |
|----------|-----|
| `DEVICE_NAME` | Debe coincidir **exactamente** con el `device_name` dado de alta en Kanvis (C4). |
| `CLOUD_ACCESS_TOKEN` | Secreto del edge en el alta (alias legacy: `CLOUD_REPORT_TOKEN`). |
| `CLOUD_REPORT_URL` | URL completa del endpoint `report-public-ip`. |
| `DEVICE_ID` | Solo local (SSID `kanvis-XXXX`, panel); **no** se envía en este POST. |

**No** uses `Authorization: Bearer` en esta petición.

Tras guardar:

```bash
sudo systemctl restart kanvis-edge
curl -H "X-API-Key: TU_API_KEY" -X POST http://127.0.0.1:8000/api/v1/connectivity/sync?force=true
```

## Endpoint (backend)

### `POST /api/v1/kanvis-edges/report-public-ip`

**Headers:** `Content-Type: application/json` (sin Bearer).

**Body:**

```json
{
  "device_name": "store-01-edge",
  "access_token": "your-secret-token-from-registration",
  "public_ip": "203.0.113.55"
}
```

**Éxito — HTTP 200:**

```json
{
  "device_name": "store-01-edge",
  "public_ip": "203.0.113.55",
  "last_public_ip_updated_at": "2026-05-19T14:32:10.123456Z"
}
```

**Errores:**

| HTTP | Significado |
|------|-------------|
| 401 | `device_name` o `access_token` incorrectos, o edge no registrado |
| 422 | Campos vacíos o tipos inválidos |
| 5xx | Reintentar con backoff |

## Comportamiento del edge (`wan_sync`)

- Obtiene la IP pública (ipify).
- Si `CLOUD_REPORT_ENABLED=true`, hace POST al URL configurado.
- En **401**: marca error en estado, no reintenta hasta corregir config (no loguea el token).
- Opcional: DDNS en paralelo (`DDNS_ENABLED`).

Estado local: `GET /api/v1/connectivity/status` (auth API key / JWT panel).

## Ejemplo curl

```bash
curl -sS -X POST "http://kanvis-pilot.ai:7777/api/v1/kanvis-edges/report-public-ip" \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "store-01-edge",
    "access_token": "REPLACE_WITH_TOKEN",
    "public_ip": "203.0.113.55"
  }'
```

## Otras credenciales

| Token | Uso |
|-------|-----|
| `API_KEY` | Nube → edge (`X-API-Key` en REST/streams del gateway) |
| `JWT_SECRET` / panel | Solo UI local del edge |

El `access_token` de reporte IP **no** sustituye al `API_KEY` para llamar al gateway.
