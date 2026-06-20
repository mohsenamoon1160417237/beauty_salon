import redis.asyncio as aioredis
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
import uuid
import asyncio

from app.core.config import settings

_redis_pool: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            str(settings.REDIS_URL),
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
    return _redis_pool


class DistributedLock:
    """
    Redis-based distributed lock using SET NX PX (atomic).
    Prevents double-booking when concurrent requests race for the same slot.

    Why this over DB transactions alone:
    - DB transactions prevent dirty reads but NOT the gap between availability-check
      and INSERT when two processes both see a slot as free simultaneously.
    - This lock closes that gap at the application layer before we touch the DB.
    """

    def __init__(self, name: str, ttl: int = settings.REDIS_LOCK_TTL):
        self.name = f"lock:{name}"
        self.ttl = ttl
        self._token: Optional[str] = None

    async def acquire(self, retry_count: int = 5, retry_delay: float = 0.2) -> bool:
        redis = await get_redis()
        self._token = str(uuid.uuid4())
        for attempt in range(retry_count):
            acquired = await redis.set(
                self.name, self._token, nx=True, ex=self.ttl
            )
            if acquired:
                return True
            if attempt < retry_count - 1:
                await asyncio.sleep(retry_delay * (2 ** attempt))  # exponential backoff
        return False

    async def release(self) -> None:
        """Release lock only if we still own it (Lua script = atomic check+delete)."""
        if not self._token:
            return
        redis = await get_redis()
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await redis.eval(lua_script, 1, self.name, self._token)
        self._token = None

    async def __aenter__(self) -> "DistributedLock":
        acquired = await self.acquire()
        if not acquired:
            raise TimeoutError(f"Could not acquire lock: {self.name}")
        return self

    async def __aexit__(self, *args) -> None:
        await self.release()


def slot_lock_key(staff_id: str, start_time: str) -> str:
    """Canonical lock key for a staff + time slot combination."""
    return f"slot:{staff_id}:{start_time}"
