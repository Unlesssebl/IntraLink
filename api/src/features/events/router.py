"""Real-time SSE Event Hub Gateway (/api/v2/events/stream and /api/v1/events/stream).

Streams Taskiq execution progress, ticket status updates, and autopilot notifications
to Web UI, CLI, and external listeners via Redis Pub/Sub.
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional

import orjson
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from core.redis_client import get_redis_client

logger = logging.getLogger("api.features.events")

router = APIRouter(tags=["Events (Real-time SSE Gateway)"])


async def sse_event_generator(
    job_id: Optional[str] = None,
    channel: str = "all",
) -> AsyncGenerator[str, None]:
    """Asynchronous generator streaming Redis Pub/Sub events as standard SSE."""
    redis = get_redis_client()
    if redis is None:
        yield ": ping\n\n"
        return

    pubsub = redis.pubsub()
    try:
        if job_id:
            channel_name = f"job:{job_id}:events"
            await pubsub.subscribe(channel_name)
            logger.debug("SSE client subscribed to channel %s", channel_name)
        else:
            await pubsub.subscribe("events:all", "channel:task_updates")
            await pubsub.psubscribe("job:*:events", "intraservice_events:*")
            logger.debug("SSE client subscribed to global event stream")

        # RFC 8895 retry directive + initial connected greeting event
        init_payload = orjson.dumps(
            {"event": "connected", "channel": channel, "job_id": job_id}
        ).decode("utf-8")
        yield f"retry: 3000\nevent: connected\ndata: {init_payload}\n\n"

        while True:
            try:
                # Direct async wait on Redis socket with 15s timeout for keep-alive ping
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if message is None:
                    # Keep-alive heartbeat comment to prevent proxy timeouts
                    yield ": ping\n\n"
                    continue

                if message.get("type") in ("message", "pmessage"):
                    raw_data = message.get("data")
                    if isinstance(raw_data, bytes):
                        str_data = raw_data.decode("utf-8")
                    else:
                        str_data = str(raw_data)

                    event_type = "message"
                    try:
                        parsed = orjson.loads(raw_data if isinstance(raw_data, (bytes, bytearray)) else str_data)
                        if isinstance(parsed, dict):
                            event_type = parsed.get("event") or parsed.get("type") or "message"
                    except Exception:
                        pass

                    yield f"event: {event_type}\ndata: {str_data}\n\n"

            except asyncio.CancelledError:
                logger.debug("SSE connection closed by client")
                break

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("Error in SSE event stream: %s", exc)
        err_payload = orjson.dumps({"event": "error", "message": str(exc)}).decode("utf-8")
        yield f"event: error\ndata: {err_payload}\n\n"
    finally:
        try:
            if job_id:
                await pubsub.unsubscribe(f"job:{job_id}:events")
            else:
                await pubsub.punsubscribe("job:*:events", "intraservice_events:*")
                await pubsub.unsubscribe("events:all", "channel:task_updates")
            await pubsub.close()
        except Exception:
            pass


@router.get("/api/v2/events/stream")
@router.get("/api/v1/events/stream")
async def event_stream(
    job_id: Optional[str] = Query(None, description="Job ID filter"),
    channel: str = Query("all", description="Event channel ('all', 'progress')"),
) -> StreamingResponse:
    """Server-Sent Events endpoint streaming live events to web clients and CLI."""
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        sse_event_generator(job_id=job_id, channel=channel),
        media_type="text/event-stream",
        headers=headers,
    )
