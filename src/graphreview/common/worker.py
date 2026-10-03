"""Generic consume → handle → publish loop with retries, DLQ and metrics.

Delivery is at-least-once: the offset is committed only after the handler (and any
publish it does) has completed. Handlers must therefore be idempotent — the aggregator
keys partial results by (job_id, agent) and the publisher guards with SETNX.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from graphreview.common.bus import Envelope, EventBus, Topics
from graphreview.common.telemetry import HANDLER_SECONDS, MESSAGES, job_id_var

log = logging.getLogger(__name__)

Handler = Callable[[Envelope], Awaitable[None]]


async def run_consumer(
    bus: EventBus,
    *,
    service: str,
    topics: list[str],
    group: str,
    handler: Handler,
    max_attempts: int = 3,
    stop: asyncio.Event | None = None,
) -> None:
    sub = await bus.subscribe(topics, group)
    log.info("%s consuming %s as group=%s", service, topics, group)
    try:
        async for env in sub:
            if stop and stop.is_set():
                break
            token = job_id_var.set(_peek_job_id(env))
            t0 = time.monotonic()
            outcome = "ok"
            for attempt in range(1, max_attempts + 1):
                try:
                    await handler(env)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if attempt == max_attempts:
                        outcome = "dlq"
                        log.exception("%s: giving up after %d attempts", service, attempt)
                        await bus.publish(
                            Topics.DLQ,
                            env.value,
                            key=env.key,
                            headers={"origin_topic": env.topic, "service": service, "error": repr(e)[:500]},
                        )
                    else:
                        outcome = "retry"
                        backoff = min(2 ** attempt, 30)
                        log.warning("%s: attempt %d failed (%r); retrying in %ss", service, attempt, e, backoff)
                        await asyncio.sleep(backoff)
            await sub.commit()
            HANDLER_SECONDS.labels(service).observe(time.monotonic() - t0)
            MESSAGES.labels(service, env.topic, outcome).inc()
            job_id_var.reset(token)
    finally:
        await sub.close()


def _peek_job_id(env: Envelope) -> str | None:
    if env.key:
        return env.key
    try:
        data = json.loads(env.value)
        return data.get("job_id") or data.get("job", {}).get("job_id")
    except Exception:
        return None
