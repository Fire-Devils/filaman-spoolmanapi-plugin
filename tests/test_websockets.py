import asyncio
import importlib
import os
import socket
import sys
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Annotated, Any
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketDisconnect, WebSocketState


@pytest.fixture
def plugin(tmp_path):
    # Load a separate package so lightweight app stubs cannot affect other tests.
    package_name = "_spoolmanapi_ws_test"
    package = ModuleType(package_name)
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "spoolmanapi")]
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}"
    engine = create_async_engine(database_url)
    database = ModuleType("app.core.database")
    database.engine = engine
    database.async_session_maker = async_sessionmaker(engine)
    deps = ModuleType("app.api.deps")

    async def get_db():
        yield None

    deps.DBSession = Annotated[Any, Depends(get_db)]
    deps.RequirePermission = lambda permission: Depends(lambda: True)
    service = ModuleType(f"{package_name}.service")
    service.SpoolmanService = object
    modules = {
        "app": ModuleType("app"), "app.core": ModuleType("app.core"),
        "app.api": ModuleType("app.api"), "app.api.deps": deps,
        "app.core.database": database, package_name: package,
        f"{package_name}.service": service,
    }
    with patch.dict(sys.modules, modules):
        router = importlib.import_module(f"{package_name}.router")
        settings = importlib.import_module(f"{package_name}.settings")
        ip_filter = importlib.import_module(f"{package_name}.ip_filter")
        ws_module = importlib.import_module(f"{package_name}.ws")
    app = FastAPI()
    app.include_router(router.router, prefix="/spoolman")
    app.include_router(router.admin_router, prefix="/api/v1")
    try:
        yield SimpleNamespace(
            app=app, router=router, settings=settings, ip_filter=ip_filter,
            database_url=database_url, ws_module=ws_module,
        )
    finally:
        asyncio.run(engine.dispose())


async def revoke_from_another_connection(plugin):
    engine = create_async_engine(plugin.database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                update(plugin.settings._settings_table)
                .where(plugin.settings._settings_table.c.id == 1)
                .values(ip_filter_enabled=True, allowed_ips=["192.0.2.1"])
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix" or not hasattr(socket, "AF_UNIX"), reason="requires Unix worker sockets")
async def test_cross_worker_delivery_checks_each_socket_and_does_not_duplicate(plugin):
    manager = type(plugin.router.websocket_manager)
    publisher, remote = manager(), manager()
    local_messages, remote_messages, denied_messages = [], [], []
    remote_delivered, denied_closed = asyncio.Event(), asyncio.Event()

    async def receive():
        return {"type": "websocket.connect"}

    def connection(ip, messages, delivered=None):
        async def send(message):
            messages.append(message)
            if delivered and message["type"] in {"websocket.send", "websocket.close"}:
                delivered.set()

        return WebSocket({"type": "websocket", "client": (ip, 50000)}, receive, send)

    local = connection("192.0.2.1", local_messages)
    allowed = connection("192.0.2.1", remote_messages, remote_delivered)
    denied = connection("192.0.2.2", denied_messages, denied_closed)
    event = SimpleNamespace(model_dump_json=lambda: '{"id":42}')
    async with (
        publisher.subscribe(("spool",), local),
        remote.subscribe((), allowed),
        remote.subscribe(("spool", "42"), denied),
    ):
        paths = [publisher._path, remote._path]
        assert all(path.is_socket() for path in paths)
        directory = publisher._path.parent
        stale = directory / f"{os.getpid()}-stale.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unused:
            unused.bind(str(stale))

        await plugin.settings.save_settings(
            plugin.settings.SpoolmanAPISettings(ip_filter_enabled=True, allowed_ips=["192.0.2.1"])
        )
        await publisher.send(("spool", "42"), event)
        await asyncio.wait_for(remote_delivered.wait(), timeout=2)
        await asyncio.wait_for(denied_closed.wait(), timeout=2)
        assert not stale.exists()

    expected = {"type": "websocket.send", "text": '{"id":42}'}
    assert local_messages[1:] == [expected]
    assert remote_messages[1:] == [expected]
    assert denied_messages[1:] == [{"type": "websocket.close", "code": 1008, "reason": "IP not allowed"}]
    assert all(not path.exists() for path in paths)
    assert not directory.stat().st_mode & 0o077


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix" or not hasattr(socket, "AF_UNIX"), reason="requires Unix worker sockets")
async def test_sequential_updates_arrive_in_order_on_remote_worker(plugin):
    manager = type(plugin.router.websocket_manager)
    publisher, remote = manager(), manager()
    first_started, release_first, second_delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    delivered = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        if message["type"] == "websocket.send":
            if message["text"] == "first":
                first_started.set()
                await release_first.wait()
            else:
                second_delivered.set()
            delivered.append(message["text"])

    async def publish():
        await publisher.send(("spool", "42"), SimpleNamespace(model_dump_json=lambda: "first"))
        await publisher.send(("spool", "42"), SimpleNamespace(model_dump_json=lambda: "second"))

    ws = WebSocket({"type": "websocket", "client": ("192.0.2.1", 50000)}, receive, send)
    async with remote.subscribe((), ws):
        task = asyncio.create_task(publish())
        try:
            await asyncio.wait_for(first_started.wait(), timeout=2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(second_delivered.wait(), timeout=0.1)
        finally:
            release_first.set()
            await asyncio.wait_for(task, timeout=2)
        assert delivered == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix" or not hasattr(socket, "AF_UNIX"), reason="requires Unix worker sockets")
async def test_slow_remote_subscriber_does_not_block_healthy_subscriber(plugin):
    manager = type(plugin.router.websocket_manager)
    publisher, remote = manager(), manager()
    slow_started, release_slow, healthy_delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        if message["type"] != "websocket.send":
            return
        if not slow_started.is_set():
            slow_started.set()
            await release_slow.wait()
        else:
            healthy_delivered.set()

    scope = {"type": "websocket", "client": ("192.0.2.1", 50000)}
    update = SimpleNamespace(model_dump_json=lambda: '{"id":42}')
    async with (
        remote.subscribe((), WebSocket(scope, receive, send)),
        remote.subscribe((), WebSocket(scope, receive, send)),
    ):
        task = asyncio.create_task(publisher.send(("spool", "42"), update))
        try:
            await asyncio.wait_for(slow_started.wait(), timeout=2)
            await asyncio.wait_for(healthy_delivered.wait(), timeout=0.5)
        finally:
            release_slow.set()
            await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix" or not hasattr(socket, "AF_UNIX"), reason="requires Unix worker sockets")
async def test_new_subscription_recovers_relay_after_failed_bind(plugin, monkeypatch):
    manager = type(plugin.router.websocket_manager)
    publisher, remote = manager(), manager()
    messages = []
    start_server = asyncio.start_unix_server

    async def fail_first_bind(*args, **kwargs):
        monkeypatch.setattr(asyncio, "start_unix_server", start_server)
        raise OSError("temporary bind failure")

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        if message["type"] == "websocket.send":
            messages.append(message["text"])

    scope = {"type": "websocket", "client": ("192.0.2.1", 50000)}
    monkeypatch.setattr(asyncio, "start_unix_server", fail_first_bind)
    async with remote.subscribe(("spool",), WebSocket(scope, receive, send)):
        async with remote.subscribe(("vendor",), WebSocket(scope, receive, send)):
            await publisher.send(("spool", "42"), SimpleNamespace(model_dump_json=lambda: '{"id":42}'))
    assert messages == ['{"id":42}']


@pytest.mark.asyncio
async def test_local_websocket_delivery_without_unix_relay(plugin, monkeypatch):
    manager = plugin.router.websocket_manager
    monkeypatch.setattr(plugin.ws_module, "_UNIX_RELAY", False)
    messages = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    ws = WebSocket({"type": "websocket", "client": ("192.0.2.1", 50000)}, receive, send)
    async with manager.subscribe((), ws):
        await manager.send(("spool", "42"), SimpleNamespace(model_dump_json=lambda: '{"id":42}'))
    assert messages[1:] == [{"type": "websocket.send", "text": '{"id":42}'}]


@pytest.mark.parametrize("delivery", ["broadcast", "ping"])
def test_open_websocket_is_revoked_after_another_worker_changes_settings(plugin, delivery):
    with TestClient(plugin.app, client=("192.0.2.2", 50000)) as client:
        assert client.put("/api/v1/admin/system/spoolman-api/settings", json={
            "ip_filter_enabled": False, "allowed_ips": [],
        }).status_code == 200
        with client.websocket_connect("/spoolman/api/v1/spool") as ws:
            client.portal.call(revoke_from_another_connection, plugin)
            if delivery == "broadcast":
                client.portal.call(plugin.router.websocket_manager._tree.send, ("spool", "42"), '{"id":42}')
            else:
                ws.send_text("ping")
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1008
        assert not plugin.router.websocket_manager._tree._pools


def test_allowed_websockets_keep_hierarchical_delivery_and_ping(plugin):
    with TestClient(plugin.app, client=("192.0.2.2", 50000)) as client:
        assert client.put("/api/v1/admin/system/spoolman-api/settings", json={
            "ip_filter_enabled": True, "allowed_ips": ["192.0.2.2"],
        }).status_code == 200
        with ExitStack() as stack:
            sockets = [stack.enter_context(client.websocket_connect("/spoolman/api/v1" + path))
                       for path in ["/", "/spool", "/spool/42"]]
            client.portal.call(plugin.router.websocket_manager._tree.send, ("spool", "42"), '{"id":42}')
            for ws in sockets:
                assert ws.receive_json() == {"id": 42}
                ws.send_text("ping")
                assert ws.receive_json() == {"status": "healthy"}
        assert not plugin.router.websocket_manager._tree._pools


@pytest.mark.parametrize("delivery", ["broadcast", "ping"])
def test_database_failure_closes_websocket_and_reconnect_restores_events(plugin, monkeypatch, delivery):
    ip_filter = plugin.ip_filter

    async def unavailable():
        raise OperationalError("SELECT spoolmanapi_settings", {}, Exception("database unavailable"))

    with TestClient(plugin.app) as client:
        with client.websocket_connect("/spoolman/api/v1/spool") as ws:
            socket = next(iter(plugin.router.websocket_manager._tree._pools[("spool",)]))
            with monkeypatch.context() as failure:
                failure.setattr(ip_filter, "load_settings", unavailable)
                if delivery == "broadcast":
                    client.portal.call(plugin.router.websocket_manager._tree.send, ("spool", "42"), '{"id":42}')
                    assert socket.application_state == WebSocketState.DISCONNECTED
                else:
                    ws.send_text("ping")
                with pytest.raises(WebSocketDisconnect) as closed:
                    ws.receive_json()
                assert closed.value.code == 1011
        assert not plugin.router.websocket_manager._tree._pools

        with client.websocket_connect("/spoolman/api/v1/spool") as ws:
            client.portal.call(plugin.router.websocket_manager._tree.send, ("spool", "42"), '{"id":43}')
            assert ws.receive_json() == {"id": 43}
            ws.send_text("ping")
            assert ws.receive_json() == {"status": "healthy"}
        assert not plugin.router.websocket_manager._tree._pools


@pytest.mark.asyncio
@pytest.mark.parametrize("close_fails", [False, True])
async def test_database_failure_isolates_socket_and_cleans_all_its_pools(plugin, monkeypatch, close_fails):
    ip_filter = plugin.ip_filter
    real_load = ip_filter.load_settings
    messages = {"first": [], "second": []}

    async def connect(name):
        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            if close_fails and message["type"] == "websocket.close":
                raise RuntimeError("socket already closed concurrently")
            messages[name].append(message)

        ws = WebSocket({"type": "websocket", "client": ("192.0.2.2", 50000)}, receive, send)
        await ws.accept()
        plugin.router.websocket_manager.connect((), ws)
        plugin.router.websocket_manager.connect(("spool",), ws)
        return ws

    sockets = {await connect("first"), await connect("second")}
    first_check = True

    async def fail_once():
        nonlocal first_check
        if first_check:
            first_check = False
            raise OperationalError("SELECT spoolmanapi_settings", {}, Exception("database unavailable"))
        return await real_load()

    monkeypatch.setattr(ip_filter, "load_settings", fail_once)
    tree = plugin.router.websocket_manager._tree
    await tree.send(("spool", "42"), '{"id":42}')
    assert sorted(sum(message["type"] == "websocket.send" for message in sent)
                  for sent in messages.values()) == [0, 1]
    if not close_fails:
        assert [message["code"] for sent in messages.values() for message in sent
                if message["type"] == "websocket.close"] == [1011]
    assert len(tree._pools[()]) == 1
    assert tree._pools[()] == tree._pools[("spool",)]
    assert tree._pools[()] < sockets
    assert sum(ws.application_state == WebSocketState.DISCONNECTED for ws in sockets) == 1
    await tree.send(("spool", "42"), '{"id":43}')
    assert sorted(sum(message["type"] == "websocket.send" for message in sent)
                  for sent in messages.values()) == [0, 2]


@pytest.mark.asyncio
async def test_database_failure_broadcast_racing_with_ping_closes_once(plugin, monkeypatch):
    messages = []
    connected = False
    ping_sent = False

    async def unavailable():
        raise OperationalError("SELECT spoolmanapi_settings", {}, Exception("database unavailable"))

    async def receive():
        nonlocal connected, ping_sent
        if not connected:
            connected = True
            return {"type": "websocket.connect"}
        assert not ping_sent, "handler kept the unsubscribed socket alive"
        ping_sent = True
        with monkeypatch.context() as failure:
            failure.setattr(plugin.ip_filter, "load_settings", unavailable)
            await plugin.router.websocket_manager._tree.send(("spool", "42"), '{"id":42}')
        return {"type": "websocket.receive", "text": "ping"}

    async def send(message):
        messages.append(message)

    ws = WebSocket({"type": "websocket", "client": ("192.0.2.2", 50000)}, receive, send)
    await plugin.router._handle_ws(ws, ("spool",))
    assert [message["type"] for message in messages] == ["websocket.accept", "websocket.close"]
    assert messages[-1]["code"] == 1011
    assert not plugin.router.websocket_manager._tree._pools


@pytest.mark.asyncio
async def test_revoked_socket_close_failure_does_not_block_allowed_delivery(plugin):
    await plugin.settings.save_settings(plugin.settings.SpoolmanAPISettings(
        ip_filter_enabled=True, allowed_ips=["192.0.2.1"],
    ))
    messages = {"192.0.2.1": [], "192.0.2.2": []}

    async def connect(ip):
        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            if ip == "192.0.2.2" and message["type"] == "websocket.close":
                raise RuntimeError("socket already closed concurrently")
            messages[ip].append(message)

        ws = WebSocket({"type": "websocket", "client": (ip, 50000)}, receive, send)
        await ws.accept()
        plugin.router.websocket_manager.connect((), ws)
        return ws

    allowed = await connect("192.0.2.1")
    revoked = await connect("192.0.2.2")
    await plugin.router.websocket_manager._tree.send(("spool", "42"), '{"id":42}')
    assert messages["192.0.2.1"][-1] == {"type": "websocket.send", "text": '{"id":42}'}
    assert not any(message["type"] == "websocket.send" for message in messages["192.0.2.2"])
    assert plugin.router.websocket_manager._tree._pools[()] == {allowed}
    assert revoked not in plugin.router.websocket_manager._tree._pools[()]


@pytest.mark.asyncio
async def test_broadcast_revocation_racing_with_ping_closes_once_and_cleans_up(plugin):
    await plugin.settings.save_settings(plugin.settings.SpoolmanAPISettings())
    messages = []
    connected = False

    async def receive():
        nonlocal connected
        if not connected:
            connected = True
            return {"type": "websocket.connect"}
        await revoke_from_another_connection(plugin)
        await plugin.router.websocket_manager._tree.send(("spool", "42"), '{"id":42}')
        return {"type": "websocket.receive", "text": "ping"}

    async def send(message):
        messages.append(message)

    ws = WebSocket({"type": "websocket", "client": ("192.0.2.2", 50000)}, receive, send)
    await plugin.router._handle_ws(ws, ("spool",))
    assert messages == [
        {"type": "websocket.accept", "subprotocol": None, "headers": []},
        {"type": "websocket.close", "code": 1008, "reason": "IP not allowed"},
    ]
    assert not plugin.router.websocket_manager._tree._pools


@pytest.mark.asyncio
async def test_asyncpg_connection_failure_closes_socket_before_removing_subscription(plugin, monkeypatch):
    await plugin.settings.save_settings(plugin.settings.SpoolmanAPISettings())
    messages = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    async def refused():
        raise ConnectionRefusedError(61, "Connection refused")

    failed_engine = create_async_engine(
        "postgresql+asyncpg://test:test@127.0.0.1:1/test", async_creator=refused,
    )
    ws = WebSocket({"type": "websocket", "client": ("192.0.2.2", 50000)}, receive, send)
    await ws.accept()
    tree = plugin.router.websocket_manager._tree
    tree.add(("spool",), ws)
    try:
        with monkeypatch.context() as failure:
            failure.setattr(plugin.settings, "async_session_maker", async_sessionmaker(failed_engine))
            await tree.send(("spool", "42"), '{"id":42}')
        assert ws.application_state == WebSocketState.DISCONNECTED
        assert messages[-1]["type"] == "websocket.close"
        assert messages[-1]["code"] == 1011
        assert not tree._pools
    finally:
        await failed_engine.dispose()


@pytest.mark.asyncio
async def test_access_check_cancellation_propagates_without_closing_socket(plugin, monkeypatch):
    async def cancelled():
        raise asyncio.CancelledError

    messages = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    ws = WebSocket({"type": "websocket", "client": ("192.0.2.2", 50000)}, receive, send)
    await ws.accept()
    monkeypatch.setattr(plugin.ip_filter, "load_settings", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await plugin.ip_filter.enforce_ws_ip_access(ws)
    assert ws.application_state == WebSocketState.CONNECTED
    assert [message["type"] for message in messages] == ["websocket.accept"]
