from fastapi import FastAPI
from contextlib import asynccontextmanager
from src.core.config import settings
from src.core.database import RedisClient
from src.api.routes import agent

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan events: Handles startup and shutdown logic.
    Ensures Redis connection is established on start and closed on stop.
    """
    # Startup: Initialize Redis
    redis = RedisClient.get_client()
    # Test connection
    await redis.ping()
    yield
    # Shutdown: Close Redis connection
    await RedisClient.close()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    lifespan=lifespan
)

# Register Routers
app.include_router(agent.router)

@app.get("/health")
async def health_check():
    """
    [CPE-2] Infrastructure Health Check.
    """
    redis = RedisClient.get_client()
    status = {"status": "healthy", "components": {"redis": "unknown"}}
    
    try:
        if await redis.ping():
            status["components"]["redis"] = "connected"
    except Exception as e:
        status["status"] = "degraded"
        status["components"]["redis"] = str(e)
        
    return status