import redis.asyncio as aioredis
from typing import Optional
from src.core.config import settings


class RedisClient:
    """Singleton Redis client manager."""
    
    _client: Optional[aioredis.Redis] = None
    
    @classmethod
    def get_client(cls) -> aioredis.Redis:
        """Get or create Redis client instance."""
        if cls._client is None:
            cls._client = aioredis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                decode_responses=True
            )
        return cls._client
    
    @classmethod
    async def close(cls):
        """Close Redis connection."""
        if cls._client:
            await cls._client.close()
            cls._client = None
