"""Cancellation-safe Redis pub/sub helpers for research WebSocket streams."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


async def iter_pubsub_until_disconnect(
    websocket: WebSocket,
    pubsub: Any,
    *,
    poll_timeout: float = 1.0,
) -> AsyncIterator[dict[str, Any]]:
    """Yield Redis messages while also consuming the WebSocket disconnect frame.

    ``pubsub.listen()`` blocks until Redis publishes an event.  A client that
    has already disconnected is therefore invisible to a handler that only
    awaits that iterator; Uvicorn then keeps the handler alive during reload.
    Keeping one ``websocket.receive()`` task alongside bounded pub/sub polls
    makes both normal disconnects and Uvicorn task cancellation deterministic.
    """
    disconnect_task = asyncio.create_task(websocket.receive())
    try:
        while True:
            message_task = asyncio.create_task(pubsub.get_message(timeout=poll_timeout))
            try:
                done, _ = await asyncio.wait(
                    {disconnect_task, message_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect_task in done:
                    try:
                        frame = disconnect_task.result()
                    except WebSocketDisconnect:
                        return
                    if frame["type"] == "websocket.disconnect":
                        return
                    # This endpoint is server-push only. If a client sends a
                    # frame, resume waiting for the eventual disconnect.
                    disconnect_task = asyncio.create_task(websocket.receive())

                if message_task in done:
                    message = message_task.result()
                    if message is not None:
                        yield message
            finally:
                if not message_task.done():
                    message_task.cancel()
                    await asyncio.gather(message_task, return_exceptions=True)
    finally:
        if not disconnect_task.done():
            disconnect_task.cancel()
            await asyncio.gather(disconnect_task, return_exceptions=True)
