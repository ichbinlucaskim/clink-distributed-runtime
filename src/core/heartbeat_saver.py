import time
import logging
import asyncio
from typing import Any, Optional, AsyncIterator, Sequence
from contextlib import asynccontextmanager

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointMetadata, CheckpointTuple
from langgraph.checkpoint.redis import RedisSaver
from redis.asyncio import Redis
from src.core.serializer import StateSerializer

logger = logging.getLogger(__name__)


class HeartbeatRedisSaver(BaseCheckpointSaver):
    """
    Wrapper around langgraph.checkpoint.redis.RedisSaver that adds heartbeat tracking.
    
    CPE-8: Every checkpoint save updates heartbeat metadata (last_seen, status, checkpoint_id).
    This enables detection of "Zombie" threads (RUNNING but stale > threshold).
    """
    
    def __init__(self, redis_url: str):
        """
        Initialize the heartbeat-enabled checkpointer.
        
        Args:
            redis_url: Redis connection string (e.g., "redis://redis:6379")
        """
        super().__init__()
        self.base_saver = RedisSaver(redis_url)
        # Access the internal Redis client from the base saver
        # Note: RedisSaver stores the client internally, we'll access it via _redis or connection
        self._redis_url = redis_url
        # Create a separate Redis client for heartbeat operations
        self._redis_client: Optional[Redis] = None
    
    async def _get_redis_client(self) -> Redis:
        """Lazy initialization of Redis client for heartbeat operations."""
        if self._redis_client is None:
            self._redis_client = Redis.from_url(self._redis_url, decode_responses=False)
        return self._redis_client
    
    async def ensure_indexes(self):
        """
        Ensure Redis Search indexes are created before use.
        
        LangGraph's RedisSaver requires checkpoints_index and checkpoint_writes_index
        to exist before any search operations. This method creates them if they don't exist.
        
        Handles "Index already exists" errors gracefully (idempotent operation).
        """
        try:
            # Check if base_saver has a setup() method (preferred approach)
            if hasattr(self.base_saver, 'setup'):
                # setup() typically creates indexes if they don't exist
                await asyncio.to_thread(self.base_saver.setup)
                logger.info("Redis indexes ensured via base_saver.setup()")
                return
            
            # Fallback: Direct index creation if setup() doesn't exist
            # Access index attributes and call create() if they exist
            if hasattr(self.base_saver, 'checkpoints_index'):
                try:
                    await asyncio.to_thread(self.base_saver.checkpoints_index.create)
                    logger.debug("checkpoints_index created/verified")
                except Exception as e:
                    error_msg = str(e).lower()
                    # Index already exists is not an error (idempotent operation)
                    if "already exists" not in error_msg and "index" not in error_msg.lower():
                        logger.warning(f"Error creating checkpoints_index: {e}")
            
            if hasattr(self.base_saver, 'checkpoint_writes_index'):
                try:
                    await asyncio.to_thread(self.base_saver.checkpoint_writes_index.create)
                    logger.debug("checkpoint_writes_index created/verified")
                except Exception as e:
                    error_msg = str(e).lower()
                    # Index already exists is not an error (idempotent operation)
                    if "already exists" not in error_msg and "index" not in error_msg.lower():
                        logger.warning(f"Error creating checkpoint_writes_index: {e}")
            
            logger.info("Redis search indexes ensured for checkpointer")
            
        except AttributeError as e:
            # If base_saver doesn't have index attributes, log and continue
            # Some versions of RedisSaver might handle this differently
            logger.warning(f"Could not access index creation methods: {e}. Indexes may be created lazily.")
        except Exception as e:
            # Log any other errors but don't fail initialization
            # Index creation failures will surface later during actual operations
            logger.warning(f"Unexpected error ensuring indexes: {e}")
    
    async def _update_heartbeat(self, thread_id: str, checkpoint_id: str):
        """
        Update heartbeat metadata for a thread.
        
        CPE-8: Stores last_seen timestamp, status (RUNNING), and latest_checkpoint_id.
        Uses atomic SET operation to prevent corruption during container kill.
        """
        redis = await self._get_redis_client()
        heartbeat_key = f"heartbeats:{thread_id}"
        
        heartbeat_data = {
            "last_seen": time.time(),
            "status": "RUNNING",
            "latest_checkpoint_id": checkpoint_id
        }
        
        serialized = StateSerializer.serialize(heartbeat_data)
        # Atomic SET operation: single operation prevents corruption during kill
        await redis.set(heartbeat_key, serialized)
        logger.debug(f"Heartbeat updated for thread {thread_id}: checkpoint {checkpoint_id}")
    
    async def force_heartbeat(self, thread_id: str, checkpoint_id: str = "starting"):
        """
        Force an immediate heartbeat update.
        
        CPE-8: Called manually to ensure "Last Active Timestamp" is updated
        immediately upon agent start, before any heavy processing begins.
        
        Args:
            thread_id: Thread ID to update heartbeat for
            checkpoint_id: Checkpoint ID (default: "starting" for initial state)
        """
        await self._update_heartbeat(thread_id, checkpoint_id)
        logger.info(f"Forced heartbeat for thread {thread_id}: {checkpoint_id}")
    
    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any],
    ) -> RunnableConfig:
        """
        Save checkpoint and update heartbeat metadata atomically.
        
        CPE-8: Every checkpoint save triggers a heartbeat update.
        Atomic operations ensure no corrupted state if container is killed during write.
        
        Wraps sync base_saver.put() using asyncio.to_thread if async methods aren't available.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint["id"]
        
        # 1. Delegate to base saver for checkpoint persistence
        # Use asyncio.to_thread to wrap sync method if async version raises NotImplementedError
        try:
            result_config = await self.base_saver.aput(config, checkpoint, metadata, new_versions)
        except NotImplementedError:
            # Fall back to wrapping sync put method in thread executor
            result_config = await asyncio.to_thread(
                self.base_saver.put, config, checkpoint, metadata, new_versions
            )
        
        # 2. Update heartbeat metadata (atomic SET operation)
        # This is safe even if container is killed mid-operation (Redis SET is atomic)
        try:
            await self._update_heartbeat(thread_id, checkpoint_id)
        except Exception as e:
            # Log but don't fail checkpoint save if heartbeat update fails
            logger.warning(f"Heartbeat update failed for thread {thread_id}: {e}")
        
        return result_config
    
    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """
        Delegate to base saver for intermediate writes.
        
        Wraps sync method using asyncio.to_thread if async version raises NotImplementedError.
        """
        try:
            await self.base_saver.aput_writes(config, writes, task_id, task_path)
        except NotImplementedError:
            # Fall back to wrapping sync put_writes method
            await asyncio.to_thread(
                self.base_saver.put_writes, config, writes, task_id, task_path
            )
    
    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """
        Delegate to base saver for checkpoint retrieval.
        
        Wraps sync method using asyncio.to_thread if async version raises NotImplementedError.
        """
        try:
            return await self.base_saver.aget_tuple(config)
        except NotImplementedError:
            # Fall back to wrapping sync get_tuple method
            return await asyncio.to_thread(self.base_saver.get_tuple, config)
    
    async def aget(self, config: RunnableConfig) -> Optional[Checkpoint]:
        """
        Async method to get checkpoint.
        
        Wraps sync method using asyncio.to_thread if async version raises NotImplementedError.
        """
        try:
            return await self.base_saver.aget(config)
        except NotImplementedError:
            # Fall back to wrapping sync get method
            return await asyncio.to_thread(self.base_saver.get, config)
        except AttributeError:
            # If base_saver doesn't have get/aget methods, return None
            return None
    
    async def alist(
        self,
        config: RunnableConfig,
        *,
        filter: Optional[dict] = None,
        before: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[CheckpointTuple]:
        """
        Async method to list checkpoints.
        
        Wraps sync method using asyncio.to_thread if async version raises NotImplementedError.
        """
        try:
            return await self.base_saver.alist(config, filter=filter, before=before, limit=limit)
        except NotImplementedError:
            # Fall back to wrapping sync list method
            return await asyncio.to_thread(
                self.base_saver.list, config, filter=filter, before=before, limit=limit
            )
        except AttributeError:
            # If base_saver doesn't have list/alist methods, return empty list
            return []
    
    def get_next_version(self, current: Optional[str], config: RunnableConfig) -> str:
        """
        Get the next version for a checkpoint.
        
        Standard LangGraph pattern: If a version exists, increment it; otherwise, start at "0".
        
        Args:
            current: Current version string (or None)
            config: Runnable config containing thread_id
        
        Returns:
            str: Next version string
        """
        try:
            # Try to delegate to base_saver's implementation
            if hasattr(self.base_saver, 'get_next_version'):
                return self.base_saver.get_next_version(current, config)
        except (NotImplementedError, AttributeError):
            pass
        
        # Fallback: Simple incrementing version logic
        if current is None:
            return "0"
        try:
            # Try to parse as integer and increment
            version_int = int(current)
            return str(version_int + 1)
        except (ValueError, TypeError):
            # If current version is not numeric, use timestamp-based versioning
            # This ensures uniqueness even if version format is unexpected
            return str(int(time.time() * 1000))  # Millisecond timestamp
    
    async def aget_next_version(
        self, 
        current: Optional[str], 
        config: RunnableConfig
    ) -> str:
        """
        Async wrapper for get_next_version.
        
        Args:
            current: Current version string (or None)
            config: Runnable config containing thread_id
        
        Returns:
            str: Next version string
        """
        try:
            # Try async version first
            if hasattr(self.base_saver, 'aget_next_version'):
                return await self.base_saver.aget_next_version(current, config)
        except (NotImplementedError, AttributeError):
            pass
        
        # Fallback: Wrap sync get_next_version in thread executor
        try:
            if hasattr(self.base_saver, 'get_next_version'):
                return await asyncio.to_thread(
                    self.base_saver.get_next_version, current, config
                )
        except (NotImplementedError, AttributeError):
            pass
        
        # Final fallback: Use our own implementation
        return self.get_next_version(current, config)
    
    def get_checkpoint(self, config: RunnableConfig) -> Optional[Checkpoint]:
        """
        Sync method to get a checkpoint.
        
        Wraps base_saver's get_checkpoint or get method.
        """
        try:
            # Try get_checkpoint first
            if hasattr(self.base_saver, 'get_checkpoint'):
                return self.base_saver.get_checkpoint(config)
            # Fallback to get method
            if hasattr(self.base_saver, 'get'):
                return self.base_saver.get(config)
        except (NotImplementedError, AttributeError):
            pass
        
        # If base_saver doesn't have these methods, raise NotImplementedError
        raise NotImplementedError("get_checkpoint not available on base_saver. Use 'aget_checkpoint' for async access.")
    
    async def aget_checkpoint(self, config: RunnableConfig) -> Optional[Checkpoint]:
        """
        Async method to get a checkpoint.
        
        Wraps base_saver's aget_checkpoint, get_checkpoint, or aget method.
        """
        try:
            # Try async version first
            if hasattr(self.base_saver, 'aget_checkpoint'):
                return await self.base_saver.aget_checkpoint(config)
        except (NotImplementedError, AttributeError):
            pass
        
        # Fallback: Try async get (aget)
        try:
            if hasattr(self.base_saver, 'aget'):
                return await self.base_saver.aget(config)
        except NotImplementedError:
            # Fallback to wrapping sync get_checkpoint or get
            if hasattr(self.base_saver, 'get_checkpoint'):
                return await asyncio.to_thread(self.base_saver.get_checkpoint, config)
            elif hasattr(self.base_saver, 'get'):
                return await asyncio.to_thread(self.base_saver.get, config)
        except AttributeError:
            pass
        
        # Final fallback: Use our existing aget implementation
        return await self.aget(config)
    
    def put(self, *args, **kwargs):
        """Sync methods not supported."""
        raise NotImplementedError("Sync methods are not supported. Use 'aput'.")
    
    def get_tuple(self, *args, **kwargs):
        """Sync methods not supported."""
        raise NotImplementedError("Sync methods are not supported. Use 'aget_tuple'.")
    
    async def close(self):
        """Cleanup Redis client connection."""
        if self._redis_client:
            await self._redis_client.close()
            self._redis_client = None
        # Note: base_saver's connection cleanup is handled by langgraph
