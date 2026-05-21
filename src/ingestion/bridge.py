"""Puente de paquetes hacia suscriptores (WebRTC, etc.)."""

from __future__ import annotations

import asyncio

from src.ingestion.buffer import RawPacket


class PacketBridge:
    """Fan-out de paquetes comprimidos a colas asyncio."""

    def __init__(self, max_queue_size: int = 500) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: list[asyncio.Queue[RawPacket]] = []

    def subscribe(self) -> asyncio.Queue[RawPacket]:
        queue: asyncio.Queue[RawPacket] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[RawPacket]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def publish(self, packet: RawPacket) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(packet)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(packet)
                except asyncio.QueueEmpty:
                    pass
