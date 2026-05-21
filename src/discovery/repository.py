"""CameraRepository: CRUD sobre JSON o SQLite."""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

import aiosqlite

from src.config_loader import AppSettings, CameraStoreBackend
from src.discovery.models import CameraRecord

logger = logging.getLogger(__name__)


class CameraRepository(ABC):
    """Interfaz CRUD del inventario de cámaras."""

    @abstractmethod
    async def list_all(self) -> list[CameraRecord]:
        ...

    @abstractmethod
    async def get(self, camera_id: str) -> CameraRecord | None:
        ...

    @abstractmethod
    async def create(self, record: CameraRecord) -> CameraRecord:
        ...

    @abstractmethod
    async def update(self, record: CameraRecord) -> CameraRecord:
        ...

    @abstractmethod
    async def delete(self, camera_id: str) -> bool:
        ...

    async def list_enabled(self) -> list[CameraRecord]:
        return [c for c in await self.list_all() if c.enabled]


class JsonCameraRepository(CameraRepository):
    """Persistencia en cameras.json con bloqueo asyncio."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def _read_raw(self) -> list[dict]:
        if not self._path.exists():
            return []
        text = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
        data = json.loads(text) if text.strip() else []
        return data if isinstance(data, list) else []

    async def _write_raw(self, items: list[dict]) -> None:
        payload = json.dumps(items, indent=2, ensure_ascii=False)

        def _write() -> None:
            self._path.write_text(payload + "\n", encoding="utf-8")

        await asyncio.to_thread(_write)

    async def list_all(self) -> list[CameraRecord]:
        async with self._lock:
            raw = await self._read_raw()
        return [CameraRecord.from_storage(item) for item in raw]

    async def get(self, camera_id: str) -> CameraRecord | None:
        for cam in await self.list_all():
            if cam.camera_id == camera_id:
                return cam
        return None

    async def create(self, record: CameraRecord) -> CameraRecord:
        async with self._lock:
            items = await self._read_raw()
            if any(i["camera_id"] == record.camera_id for i in items):
                raise ValueError(f"camera_id ya existe: {record.camera_id}")
            items.append(record.model_dump_for_storage())
            await self._write_raw(items)
        return record

    async def update(self, record: CameraRecord) -> CameraRecord:
        async with self._lock:
            items = await self._read_raw()
            found = False
            for idx, item in enumerate(items):
                if item["camera_id"] == record.camera_id:
                    items[idx] = record.model_dump_for_storage()
                    found = True
                    break
            if not found:
                raise KeyError(f"camera_id no encontrado: {record.camera_id}")
            await self._write_raw(items)
        return record

    async def delete(self, camera_id: str) -> bool:
        async with self._lock:
            items = await self._read_raw()
            new_items = [i for i in items if i["camera_id"] != camera_id]
            if len(new_items) == len(items):
                return False
            await self._write_raw(new_items)
        return True


class SqliteCameraRepository(CameraRepository):
    """Persistencia en SQLite con JSON completo por cámara."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS cameras (
        camera_id TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 1,
        config_json TEXT NOT NULL
    );
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._lock = asyncio.Lock()

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(self._SCHEMA)
            await self._migrate_legacy_table(db)
            await db.commit()
        self._initialized = True

    async def _migrate_legacy_table(self, db: aiosqlite.Connection) -> None:
        """Migra tabla legacy plana a config_json si existe."""
        async with db.execute("PRAGMA table_info(cameras)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if not cols or "config_json" in cols:
            return
        if "ip_address" not in cols:
            return
        await db.execute("ALTER TABLE cameras RENAME TO cameras_legacy")
        await db.execute(self._SCHEMA)
        async with db.execute("SELECT * FROM cameras_legacy") as cur:
            db.row_factory = aiosqlite.Row
            rows = await cur.fetchall()
        for row in rows:
            record = CameraRecord.from_storage(dict(row))
            await db.execute(
                "INSERT INTO cameras (camera_id, enabled, config_json) VALUES (?, ?, ?)",
                (
                    record.camera_id,
                    1 if record.enabled else 0,
                    json.dumps(record.model_dump_for_storage()),
                ),
            )
        await db.execute("DROP TABLE cameras_legacy")

    def _row_to_record(self, row: aiosqlite.Row) -> CameraRecord:
        data = json.loads(row["config_json"])
        data["camera_id"] = row["camera_id"]
        data["enabled"] = bool(row["enabled"])
        return CameraRecord.from_storage(data)

    async def list_all(self) -> list[CameraRecord]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT camera_id, enabled, config_json FROM cameras ORDER BY camera_id"
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_record(r) for r in rows]

    async def get(self, camera_id: str) -> CameraRecord | None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT camera_id, enabled, config_json FROM cameras WHERE camera_id = ?",
                (camera_id,),
            ) as cur:
                row = await cur.fetchone()
        return self._row_to_record(row) if row else None

    async def create(self, record: CameraRecord) -> CameraRecord:
        await self._ensure_schema()
        payload = json.dumps(record.model_dump_for_storage())
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT INTO cameras (camera_id, enabled, config_json) VALUES (?, ?, ?)",
                    (record.camera_id, 1 if record.enabled else 0, payload),
                )
                await db.commit()
        except aiosqlite.IntegrityError as exc:
            raise ValueError(f"camera_id ya existe: {record.camera_id}") from exc
        return record

    async def update(self, record: CameraRecord) -> CameraRecord:
        await self._ensure_schema()
        payload = json.dumps(record.model_dump_for_storage())
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """
                UPDATE cameras SET enabled=?, config_json=? WHERE camera_id=?
                """,
                (1 if record.enabled else 0, payload, record.camera_id),
            )
            await db.commit()
            if cur.rowcount == 0:
                raise KeyError(f"camera_id no encontrado: {record.camera_id}")
        return record

    async def delete(self, camera_id: str) -> bool:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "DELETE FROM cameras WHERE camera_id = ?", (camera_id,)
            )
            await db.commit()
            return cur.rowcount > 0


def create_camera_repository(settings: AppSettings) -> CameraRepository:
    """Factory según backend configurado."""
    if settings.camera_store_backend == CameraStoreBackend.SQLITE:
        return SqliteCameraRepository(settings.resolved_cameras_db)
    return JsonCameraRepository(settings.resolved_cameras_json)
