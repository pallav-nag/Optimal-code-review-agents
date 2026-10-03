"""Small key/value + sorted-set state API backed by Redis (prod) or a dict (tests/demo).

Used for: job status, aggregator partial results and deadlines, idempotency keys,
and code-graph blobs shared between the indexer and the context service.
"""
from __future__ import annotations

import time
from typing import Protocol


class StateStore(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes | str, ttl_s: int | None = None) -> None: ...
    async def set_nx(self, key: str, value: bytes | str, ttl_s: int | None = None) -> bool: ...
    async def delete(self, *keys: str) -> None: ...
    async def hset(self, key: str, field: str, value: bytes | str, ttl_s: int | None = None) -> None: ...
    async def hgetall(self, key: str) -> dict[str, bytes]: ...
    async def zadd(self, key: str, member: str, score: float) -> None: ...
    async def zdue(self, key: str, max_score: float) -> list[str]: ...
    async def zrem(self, key: str, member: str) -> bool: ...
    async def zlatest(self, key: str, n: int) -> list[str]: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


def _b(v: bytes | str) -> bytes:
    return v if isinstance(v, bytes) else v.encode()


class MemoryStore:
    def __init__(self):
        self._kv: dict[str, tuple[bytes, float | None]] = {}
        self._h: dict[str, dict[str, bytes]] = {}
        self._z: dict[str, dict[str, float]] = {}

    def _alive(self, key: str) -> bool:
        item = self._kv.get(key)
        if item and item[1] is not None and item[1] < time.time():
            del self._kv[key]
            return False
        return item is not None

    async def get(self, key):
        return self._kv[key][0] if self._alive(key) else None

    async def set(self, key, value, ttl_s=None):
        self._kv[key] = (_b(value), time.time() + ttl_s if ttl_s else None)

    async def set_nx(self, key, value, ttl_s=None):
        if self._alive(key):
            return False
        await self.set(key, value, ttl_s)
        return True

    async def delete(self, *keys):
        for k in keys:
            self._kv.pop(k, None)
            self._h.pop(k, None)
            self._z.pop(k, None)

    async def hset(self, key, field, value, ttl_s=None):
        self._h.setdefault(key, {})[field] = _b(value)

    async def hgetall(self, key):
        return dict(self._h.get(key, {}))

    async def zadd(self, key, member, score):
        self._z.setdefault(key, {})[member] = score

    async def zdue(self, key, max_score):
        return [m for m, s in sorted(self._z.get(key, {}).items(), key=lambda x: x[1]) if s <= max_score]

    async def zrem(self, key, member):
        return self._z.get(key, {}).pop(member, None) is not None

    async def zlatest(self, key, n):
        return [m for m, _ in sorted(self._z.get(key, {}).items(), key=lambda x: -x[1])[:n]]

    async def ping(self):
        return True

    async def close(self):
        pass


class RedisStore:
    def __init__(self, url: str):
        import redis.asyncio as aioredis

        self.r = aioredis.from_url(url, decode_responses=False)

    async def get(self, key):
        return await self.r.get(key)

    async def set(self, key, value, ttl_s=None):
        await self.r.set(key, _b(value), ex=ttl_s)

    async def set_nx(self, key, value, ttl_s=None):
        return bool(await self.r.set(key, _b(value), ex=ttl_s, nx=True))

    async def delete(self, *keys):
        if keys:
            await self.r.delete(*keys)

    async def hset(self, key, field, value, ttl_s=None):
        async with self.r.pipeline(transaction=True) as p:
            p.hset(key, field, _b(value))
            if ttl_s:
                p.expire(key, ttl_s)
            await p.execute()

    async def hgetall(self, key):
        return {k.decode(): v for k, v in (await self.r.hgetall(key)).items()}

    async def zadd(self, key, member, score):
        await self.r.zadd(key, {member: score})

    async def zdue(self, key, max_score):
        return [m.decode() for m in await self.r.zrangebyscore(key, "-inf", max_score)]

    async def zrem(self, key, member):
        # ZREM's return value doubles as a cross-replica lock: only one remover wins.
        return bool(await self.r.zrem(key, member))

    async def zlatest(self, key, n):
        return [m.decode() for m in await self.r.zrevrange(key, 0, n - 1)]

    async def ping(self):
        return bool(await self.r.ping())

    async def close(self):
        await self.r.aclose()


def make_store(settings) -> StateStore:
    if settings.state_backend == "memory":
        return MemoryStore()
    return RedisStore(settings.redis_url)
