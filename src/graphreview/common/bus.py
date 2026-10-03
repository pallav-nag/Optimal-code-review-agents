"""Event bus abstraction.

`KafkaBus` is the production transport (aiokafka, idempotent producer, manual commits →
at-least-once). `MemoryBus` mirrors Kafka's semantics in-process — per-topic append-only
logs, consumer groups with shared offsets — so the full pipeline runs in tests and the
local demo without a broker.

Fan-out works the Kafka way: every agent role subscribes to `pr.review.requested` with its
own consumer group, so each role sees every job while replicas of a role split partitions.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

log = logging.getLogger(__name__)


class Topics:
    INDEX_REQUESTED = "repo.index.requested"
    INDEXED = "repo.indexed"
    REVIEW_REQUESTED = "pr.review.requested"
    AGENT_RESULTS = "review.agent.results"
    AGGREGATED = "review.aggregated"
    COMPLETED = "review.completed"
    DLQ = "review.dlq"

    ALL = (INDEX_REQUESTED, INDEXED, REVIEW_REQUESTED, AGENT_RESULTS, AGGREGATED, COMPLETED, DLQ)


@dataclass
class Envelope:
    topic: str
    key: str | None
    value: bytes
    headers: dict[str, str] = field(default_factory=dict)
    partition: int = 0
    offset: int = 0


class Subscription(Protocol):
    def __aiter__(self) -> AsyncIterator[Envelope]: ...
    async def commit(self) -> None: ...
    async def close(self) -> None: ...


class EventBus(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, topic: str, value: BaseModel | bytes, key: str | None = None,
                      headers: dict[str, str] | None = None) -> None: ...
    async def subscribe(self, topics: list[str], group: str) -> Subscription: ...


def _encode(value: BaseModel | bytes) -> bytes:
    return value if isinstance(value, bytes) else value.model_dump_json().encode()


# ── Kafka ────────────────────────────────────────────────────────────────────


class _KafkaSubscription:
    def __init__(self, consumer):
        self._consumer = consumer

    async def __aiter__(self):
        async for msg in self._consumer:
            yield Envelope(
                topic=msg.topic,
                key=msg.key.decode() if msg.key else None,
                value=msg.value,
                headers={k: v.decode() for k, v in (msg.headers or [])},
                partition=msg.partition,
                offset=msg.offset,
            )

    async def commit(self) -> None:
        await self._consumer.commit()

    async def close(self) -> None:
        await self._consumer.stop()


async def _connect(what: str, make, attempts: int = 30):
    """Start a Kafka client, retrying while the broker comes up (pods often start before Kafka)."""
    delay = 1.0
    for attempt in range(1, attempts + 1):
        client = make()
        try:
            await client.start()
            return client
        except Exception as e:
            await client.stop()
            if attempt == attempts:
                raise
            log.warning("%s: Kafka not reachable (%r); retry %d/%d in %.0fs", what, e, attempt, attempts, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)


class KafkaBus:
    def __init__(self, bootstrap: str, client_id: str = "graphreview", max_poll_interval_ms: int = 600_000):
        self.bootstrap = bootstrap
        self.client_id = client_id
        self.max_poll_interval_ms = max_poll_interval_ms
        self._producer = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = await _connect("producer", lambda: AIOKafkaProducer(
            bootstrap_servers=self.bootstrap,
            client_id=self.client_id,
            acks="all",
            enable_idempotence=True,
            compression_type="gzip",
            max_request_size=8 * 1024 * 1024,
        ))

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def publish(self, topic, value, key=None, headers=None) -> None:
        assert self._producer, "bus not started"
        await self._producer.send_and_wait(
            topic,
            _encode(value),
            key=key.encode() if key else None,
            headers=[(k, v.encode()) for k, v in (headers or {}).items()],
        )

    async def subscribe(self, topics: list[str], group: str) -> Subscription:
        from aiokafka import AIOKafkaConsumer

        consumer = await _connect(f"consumer {group}", lambda: AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap,
            group_id=group,
            client_id=f"{self.client_id}-{group}",
            enable_auto_commit=False,  # commit only after the handler succeeded
            auto_offset_reset="earliest",
            max_poll_interval_ms=self.max_poll_interval_ms,
            max_poll_records=1,
            fetch_max_bytes=8 * 1024 * 1024,
        ))
        return _KafkaSubscription(consumer)


# ── In-memory ────────────────────────────────────────────────────────────────


class _MemorySubscription:
    def __init__(self, bus: MemoryBus, topics: list[str], group: str):
        self.bus, self.topics, self.group = bus, topics, group
        self._pending: tuple[str, int] | None = None
        self._closed = False

    async def __aiter__(self):
        while not self._closed:
            env = await self.bus._next(self.topics, self.group)
            if env is None:
                return
            self._pending = (env.topic, env.offset)
            yield env

    async def commit(self) -> None:
        if self._pending:
            topic, offset = self._pending
            self.bus._committed[(self.group, topic)] = max(
                self.bus._committed[(self.group, topic)], offset + 1
            )
            self._pending = None

    async def close(self) -> None:
        self._closed = True
        async with self.bus._cond:
            self.bus._cond.notify_all()


class MemoryBus:
    def __init__(self):
        self._logs: dict[str, list[Envelope]] = defaultdict(list)
        self._claimed: dict[tuple[str, str], int] = defaultdict(int)  # next offset to hand out
        self._committed: dict[tuple[str, str], int] = defaultdict(int)
        self._cond = asyncio.Condition()
        self._stopped = False

    async def start(self) -> None:
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        async with self._cond:
            self._cond.notify_all()

    async def publish(self, topic, value, key=None, headers=None) -> None:
        async with self._cond:
            log_ = self._logs[topic]
            log_.append(Envelope(topic, key, _encode(value), dict(headers or {}), 0, len(log_)))
            self._cond.notify_all()

    async def subscribe(self, topics: list[str], group: str) -> Subscription:
        return _MemorySubscription(self, topics, group)

    async def _next(self, topics: list[str], group: str) -> Envelope | None:
        async with self._cond:
            while True:
                if self._stopped:
                    return None
                for t in topics:
                    pos = self._claimed[(group, t)]
                    if pos < len(self._logs[t]):
                        self._claimed[(group, t)] = pos + 1
                        return self._logs[t][pos]
                await self._cond.wait()

    def messages(self, topic: str) -> list[Envelope]:
        """Test helper: everything ever published to `topic`."""
        return list(self._logs[topic])


def make_bus(settings, client_id: str = "graphreview") -> EventBus:
    if settings.bus_backend == "memory":
        return MemoryBus()
    return KafkaBus(settings.kafka_bootstrap, client_id, settings.kafka_max_poll_interval_ms)
