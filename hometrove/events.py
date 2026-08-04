"""Tiny in-process pub/sub used by SSE.

Workers update progress; GET /api/jobs/stream fans it out to subscribed
browsers. M0 keeps it as a single asyncio.Queue per subscriber.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from typing import AsyncIterator


@dataclass
class JobEvent:
    type: str           # 'progress' | 'job-update' | 'heartbeat'
    ts: float
    payload: dict

    def to_sse(self) -> str:
        import json

        return f"event: {self.type}\ndata: {json.dumps(self.payload, ensure_ascii=False)}\n\n"


class EventBus:
    def __init__(self) -> None:
        self._subs: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subs.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    async def publish(self, event: JobEvent) -> None:
        subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the slowest consumer — we don't want slow clients to block workers.
                pass

    async def stream(self, q: asyncio.Queue) -> AsyncIterator[str]:
        last_heartbeat = time.monotonic()
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15.0)
                yield ev.to_sse()
            except asyncio.TimeoutError:
                ev = JobEvent(type="heartbeat", ts=time.time(), payload={})
                yield ev.to_sse()


BUS = EventBus()
