import os
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_redis_host() -> str:
    """
    Determine Redis host based on execution environment.
    
    - Inside Docker: Use service name 'redis' (Docker network DNS)
    - On Host (tests/local dev): Use 'localhost' (port-forwarded from Docker)
    
    Detection: Check for IS_DOCKER env var or container detection.
    """
    # Explicit environment variable override
    if os.getenv("REDIS_HOST"):
        return os.getenv("REDIS_HOST")
    
    # Check if running inside Docker container
    is_docker = os.getenv("IS_DOCKER", "").lower() in ("1", "true", "yes")
    if is_docker:
        return "redis"  # Docker service name
    
    # Check for common Docker indicators
    if os.path.exists("/.dockerenv"):
        return "redis"  # Running inside Docker
    
    # Default: Assume host machine (tests, local dev)
    return "localhost"


class Settings(BaseSettings):
    PROJECT_NAME: str = "Clink Distributed Runtime"
    REDIS_HOST: str = _get_redis_host()  # Auto-detect: 'redis' in Docker, 'localhost' on host
    REDIS_PORT: int = 6379
    
    # Pydantic V2 Style Configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # Ignore extra env variables
    )

settings = Settings()
