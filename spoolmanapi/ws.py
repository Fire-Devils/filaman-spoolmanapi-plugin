"""WebSocket connection manager with hierarchical subscription tree.

Mirrors Spoolman's WebSocket push architecture so that external tools
(Moonraker, OctoPrint, etc.) receive real-time notifications for
spool/filament/vendor CRUD events.

Architecture:
  - SubscriptionTree: maps pool tuples to sets of WebSocket connections.
    A pool is a tuple like ``()``, ``("spool",)``, ``("spool", "42")``.
    Broadcasting an event to ``("spool", "42")`` also delivers it to
    ``("spool",)`` and ``()`` — matching Spoolman's hierarchical model.
  - WebSocketManager: relays events to local subscribers and other workers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import stat
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import blake2s
from pathlib import Path
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from . import settings
from .ip_filter import enforce_ws_ip_access

logger = logging.getLogger(__name__)
_UNIX_RELAY = os.name == "posix" and hasattr(socket, "AF_UNIX")


def _worker_directory(*, create: bool = False) -> Path:
    # A short pathname fits Unix sockets even when the database path is long.
    identity = f"{Path.cwd()}\0{settings.engine.url.render_as_string()}".encode()
    key = blake2s(identity, digest_size=8).hexdigest()
    path = Path("/tmp") / f"spoolmanapi-{os.getuid()}-{key}"
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError(f"Spoolman worker directory must be private: {path}")
    return path


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

        async def deliver(ws: WebSocket) -> None:
            try:
                if (ws.client_state != WebSocketState.DISCONNECTED
                        and ws.application_state != WebSocketState.DISCONNECTED):
                    if await enforce_ws_ip_access(ws):
                        await ws.send_text(data)
                        return
            except Exception:
                logger.debug("Failed to send WS message, removing connection")
            # Remove closed or denied sockets even when closing failed.
            for subscribed_pool in tuple(self._pools):
                self.remove(subscribed_pool, ws)

        await asyncio.gather(*(deliver(ws) for ws in targets))


class WebSocketManager:
    """Relay events to the workers with active WebSocket subscriptions."""

    def __init__(self) -> None:
        self._tree = SubscriptionTree()
        self._lock = asyncio.Lock()
        self._subscriptions = 0
        self._listener: asyncio.Server | None = None
        self._path: Path | None = None

    @asynccontextmanager
    async def subscribe(self, pool: tuple[str, ...], ws: WebSocket) -> AsyncIterator[None]:
        async with self._lock:
            if self._listener is None and _UNIX_RELAY:
                try:
                    directory = _worker_directory(create=True)
                    for peer in directory.iterdir():
                        if peer.suffix not in {".sock", ".starting"}:
                            continue
                        pid = peer.name.partition("-")[0]
                        if not pid.isdecimal():
                            continue
                        try:
                            os.kill(int(pid), 0)
                        except ProcessLookupError:
                            peer.unlink(missing_ok=True)
                        except PermissionError:
                            continue
                    path = directory / f"{os.getpid()}-{uuid4().hex[:12]}.sock"
                    staged = path.with_suffix(".starting")
                    try:
                        listener = await asyncio.start_unix_server(self._receive, path=staged)
                        try:
                            # Peers must never see a bound socket before it is listening.
                            staged.replace(path)
                        except OSError:
                            listener.close()
                            raise
                    finally:
                        staged.unlink(missing_ok=True)
                    self._listener, self._path = listener, path
                except OSError:
                    logger.warning("Cannot listen for Spoolman events from other workers", exc_info=True)
            self._subscriptions += 1
        try:
            await ws.accept()
            self.connect(pool, ws)
            yield
        finally:
            self.disconnect(pool, ws)
            async with self._lock:
                self._subscriptions -= 1
                if not self._subscriptions and self._listener is not None and self._path is not None:
                    self._listener.close()
                    self._path.unlink(missing_ok=True)
                    self._listener = self._path = None

    async def _receive(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(2):
                pool, data = json.loads(await reader.read())
                if isinstance(pool, list) and all(isinstance(part, str) for part in pool) and isinstance(data, str):
                    await self._tree.send(tuple(pool), data)
        except (TypeError, ValueError, OSError, TimeoutError):
            logger.debug("Could not receive Spoolman worker event", exc_info=True)
        finally:
            writer.close()

    async def _forward(self, pool: tuple[str, ...], data: str) -> None:
        if not _UNIX_RELAY:
            return
        try:
            directory = _worker_directory()
        except FileNotFoundError:
            return
        except OSError:
            logger.warning("Cannot locate Spoolman worker sockets", exc_info=True)
            return
        message = json.dumps((pool, data)).encode()

        async def deliver(peer: Path) -> None:
            try:
                async with asyncio.timeout(2):
                    reader, writer = await asyncio.open_unix_connection(path=peer)
                    try:
                        writer.write(message)
                        await writer.drain()
                        writer.write_eof()
                        # Wait for delivery so the next publish cannot overtake this one.
                        await reader.read()
                    finally:
                        writer.close()
                    await writer.wait_closed()
            except (FileNotFoundError, ConnectionRefusedError):
                peer.unlink(missing_ok=True)
            except (OSError, TimeoutError):
                logger.debug("Could not send Spoolman worker event to %s", peer, exc_info=True)

        await asyncio.gather(*(deliver(peer) for peer in directory.glob("*.sock") if peer != self._path))

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
        await asyncio.gather(self._tree.send(pool, data), self._forward(pool, data))


# Module-level singleton — import and use throughout the plugin.
websocket_manager = WebSocketManager()
