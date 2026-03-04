"""WebSocket connection manager with hierarchical subscription tree.

Mirrors Spoolman's WebSocket push architecture so that external tools
(Moonraker, OctoPrint, etc.) receive real-time notifications for
spool/filament/vendor CRUD events.

Architecture:
  - SubscriptionTree: maps pool tuples to sets of WebSocket connections.
    A pool is a tuple like ``()``, ``("spool",)``, ``("spool", "42")``.
    Broadcasting an event to ``("spool", "42")`` also delivers it to
    ``("spool",)`` and ``()`` — matching Spoolman's hierarchical model.
  - WebSocketManager: thin wrapper exposing connect/disconnect/send.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


class SubscriptionTree:
    """Hierarchical mapping of pool keys → active WebSocket connections."""

    def __init__(self) -> None:
        self._pools: dict[tuple[str, ...], set[WebSocket]] = defaultdict(set)

    def add(self, pool: tuple[str, ...], ws: WebSocket) -> None:
        self._pools[pool].add(ws)

    def remove(self, pool: tuple[str, ...], ws: WebSocket) -> None:
        bucket = self._pools.get(pool)
        if bucket is not None:
            bucket.discard(ws)
            if not bucket:
                del self._pools[pool]

    async def send(self, pool: tuple[str, ...], data: str) -> None:
        """Broadcast *data* to all subscribers of *pool* and its parents.

        E.g. pool ``("spool", "42")`` fans out to pools
        ``("spool", "42")``, ``("spool",)`` and ``()``.
        """
        targets: set[WebSocket] = set()
        # Collect subscribers from the exact pool and every parent prefix.
        for length in range(len(pool) + 1):
            prefix = pool[:length]
            bucket = self._pools.get(prefix)
            if bucket:
                targets.update(bucket)

        dead: list[tuple[tuple[str, ...], WebSocket]] = []
        for ws in targets:
            if ws.client_state == WebSocketState.DISCONNECTED:
                # Find which pool(s) this dead socket belongs to and mark.
                for p, bucket in self._pools.items():
                    if ws in bucket:
                        dead.append((p, ws))
                continue
            try:
                await ws.send_text(data)
            except Exception:
                logger.debug("Failed to send WS message, removing connection")
                for p, bucket in self._pools.items():
                    if ws in bucket:
                        dead.append((p, ws))

        # Clean up dead connections.
        for p, ws in dead:
            self.remove(p, ws)


class WebSocketManager:
    """Singleton-style manager wrapping the subscription tree."""

    def __init__(self) -> None:
        self._tree = SubscriptionTree()

    def connect(self, pool: tuple[str, ...], ws: WebSocket) -> None:
        self._tree.add(pool, ws)
        logger.debug("WS connected to pool %s", pool)

    def disconnect(self, pool: tuple[str, ...], ws: WebSocket) -> None:
        self._tree.remove(pool, ws)
        logger.debug("WS disconnected from pool %s", pool)

    async def send(self, pool: tuple[str, ...], event: object) -> None:
        """Serialize *event* and broadcast to the pool hierarchy.

        *event* should be a Pydantic model exposing ``model_dump_json()``.
        """
        data = event.model_dump_json()  # type: ignore[union-attr]
        await self._tree.send(pool, data)


# Module-level singleton — import and use throughout the plugin.
websocket_manager = WebSocketManager()
