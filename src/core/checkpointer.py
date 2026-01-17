from typing import Any, Optional, AsyncIterator, Sequence
from contextlib import asynccontextmanager
import time

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointMetadata, CheckpointTuple
from src.core.database import RedisClient
from src.core.serializer import StateSerializer

class RedisSaver(BaseCheckpointSaver):
    """
    Custom Redis Implementation for LangGraph Persistence.
    
    Why Custom?
    - Full control over key schema (e.g., 'checkpoint:{thread_id}:{checkpoint_id}')
    - Optimized serialization using 'orjson'
    - Direct integration with our Singleton RedisClient
    """

    def __init__(self):
        super().__init__()
        self.redis = RedisClient.get_client()

    @classmethod
    @asynccontextmanager
    async def from_conn_info(cls) -> AsyncIterator["RedisSaver"]:
        """
        Factory method to instantiate the saver.
        """
        saver = cls()
        yield saver

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any],
    ) -> RunnableConfig:
        """
        [Async] Save a checkpoint to Redis AND update Heartbeat.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint["id"]
        
        # 1. Serialize the payload
        data = {
            "checkpoint": checkpoint,
            "metadata": metadata,
            "new_versions": new_versions,
        }
        serialized_data = StateSerializer.serialize(data)

        # 2. Define Redis Keys
        key = f"checkpoints:{thread_id}:{checkpoint_id}"
        latest_key = f"checkpoints:{thread_id}:latest"
        heartbeat_key = f"heartbeats:{thread_id}"  # Heartbeat Key

        # 3. Write to Redis (Pipeline recommended for atomicity)
        # Save Checkpoint
        await self.redis.set(key, serialized_data)
        await self.redis.set(latest_key, checkpoint_id)
        
        # Update Heartbeat (Timestamp + Status)
        # We assume if saving, it's 'RUNNING'
        heartbeat_data = {
            "last_seen": time.time(),
            "status": "RUNNING",
            "latest_checkpoint_id": checkpoint_id
        }
        await self.redis.set(heartbeat_key, StateSerializer.serialize(heartbeat_data))

        # 4. Return updated config
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """
        [Async] Store intermediate writes linked to a checkpoint.
        Required by LangGraph to persist node outputs before the step finishes.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"]["checkpoint_id"]
        
        # Key schema: writes:{thread_id}:{checkpoint_id}:{task_id}
        key = f"writes:{thread_id}:{checkpoint_id}:{task_id}"
        
        # Serialize the writes list
        serialized_writes = StateSerializer.serialize(writes)
        
        await self.redis.set(key, serialized_writes)

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """
        [Async] Retrieve the latest checkpoint tuple for a given thread.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"].get("checkpoint_id")

        # 1. Determine which checkpoint to load
        if not checkpoint_id:
            latest_key = f"checkpoints:{thread_id}:latest"
            checkpoint_id = await self.redis.get(latest_key)
            
        if not checkpoint_id:
            return None

        # 2. Fetch Data
        key = f"checkpoints:{thread_id}:{checkpoint_id}"
        raw_data = await self.redis.get(key)
        
        if not raw_data:
            return None

        # 3. Deserialize
        data = StateSerializer.deserialize(raw_data)
        
        # 4. Reconstruct CheckpointTuple
        return CheckpointTuple(
            config=config,
            checkpoint=data["checkpoint"],
            metadata=data.get("metadata", {}),
            parent_config=None, 
        )
        
    def put(self, *args, **kwargs):
        raise NotImplementedError("Sync methods are not supported. Use 'aput'.")

    def get_tuple(self, *args, **kwargs):
        raise NotImplementedError("Sync methods are not supported. Use 'aget_tuple'.")