import asyncio

from graphreview.common.bus import MemoryBus, Topics
from graphreview.common.worker import run_consumer


async def _drain(bus, topic, group, n, out):
    sub = await bus.subscribe([topic], group)
    async for env in sub:
        out.append((group, env.value))
        await sub.commit()
        if len(out) >= n:
            break


async def test_consumer_groups_fan_out():
    bus = MemoryBus()
    for i in range(3):
        await bus.publish("t", f"m{i}".encode(), key=str(i))
    a, b = [], []
    await asyncio.wait_for(asyncio.gather(_drain(bus, "t", "g1", 3, a), _drain(bus, "t", "g2", 3, b)), 1)
    assert [v for _, v in a] == [v for _, v in b] == [b"m0", b"m1", b"m2"]  # every group sees every message


async def test_same_group_competes():
    bus = MemoryBus()
    seen = []

    async def worker():
        sub = await bus.subscribe(["t"], "g")
        async for env in sub:
            seen.append(env.value)
            await sub.commit()

    tasks = [asyncio.create_task(worker()) for _ in range(2)]
    for i in range(10):
        await bus.publish("t", str(i).encode())
    await asyncio.sleep(0.05)
    await bus.stop()
    await asyncio.gather(*tasks)
    assert sorted(seen, key=int) == [str(i).encode() for i in range(10)]  # each delivered exactly once


async def test_failing_handler_goes_to_dlq(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    bus = MemoryBus()
    calls = 0

    async def handler(env):
        nonlocal calls
        calls += 1
        raise ValueError("boom")

    await bus.publish(Topics.AGGREGATED, b'{"job": {"job_id": "j1"}}', key="j1")
    task = asyncio.create_task(run_consumer(bus, service="t", topics=[Topics.AGGREGATED], group="g",
                                            handler=handler, max_attempts=3))
    for _ in range(100):
        if bus.messages(Topics.DLQ):
            break
        await _real_sleep(0.01)
    await bus.stop()
    await asyncio.gather(task, return_exceptions=True)
    assert calls == 3
    dlq = bus.messages(Topics.DLQ)
    assert len(dlq) == 1 and dlq[0].headers["origin_topic"] == Topics.AGGREGATED and "boom" in dlq[0].headers["error"]


_real_sleep = asyncio.sleep


async def _no_sleep(_):
    await _real_sleep(0)


async def test_kafka_connect_retries_until_broker_is_up(monkeypatch):
    from graphreview.common import bus as busmod

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    attempts = []

    class Flaky:
        async def start(self):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("no broker")

        async def stop(self):
            pass

    client = await busmod._connect("test", Flaky)
    assert isinstance(client, Flaky) and len(attempts) == 3
