import hashlib
import json
import logging
from typing import Any, Dict, Optional
from redis.asyncio import Redis
from src.core.serializer import StateSerializer

logger = logging.getLogger(__name__)


class IdempotencyTracker:
    """
    CPE-9: Idempotency tracking for tool executions.
    
    Prevents duplicate side effects during agent resurrection by tracking
    executed tool calls using (checkpoint_id, tool_name, args_hash) as unique identifiers.
    """
    
    def __init__(self, redis_client: Redis):
        """
        Initialize idempotency tracker.
        
        Args:
            redis_client: Redis async client instance
        """
        self.redis = redis_client
        self.key_prefix = "idempotency:tool_executions"
    
    def _generate_execution_id(self, checkpoint_id: str, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        Generate a unique execution ID for a tool call.
        
        Args:
            checkpoint_id: The checkpoint ID when the tool was called
            tool_name: Name of the tool being executed
            tool_args: Arguments passed to the tool (must be JSON-serializable)
            
        Returns:
            str: Unique execution identifier
        """
        # Create a deterministic hash from checkpoint_id, tool_name, and normalized args
        args_normalized = json.dumps(tool_args, sort_keys=True)
        combined = f"{checkpoint_id}:{tool_name}:{args_normalized}"
        execution_hash = hashlib.sha256(combined.encode()).hexdigest()
        return f"{self.key_prefix}:{execution_hash}"
    
    async def is_executed(self, checkpoint_id: str, tool_name: str, tool_args: Dict[str, Any]) -> bool:
        """
        Check if a tool execution has already been performed.
        
        Args:
            checkpoint_id: The checkpoint ID when the tool was called
            tool_name: Name of the tool being executed
            tool_args: Arguments passed to the tool
            
        Returns:
            bool: True if already executed, False otherwise
        """
        execution_id = self._generate_execution_id(checkpoint_id, tool_name, tool_args)
        exists = await self.redis.exists(execution_id)
        
        if exists:
            logger.info(f"Idempotency check: Tool {tool_name} already executed for checkpoint {checkpoint_id}")
        else:
            logger.debug(f"Idempotency check: Tool {tool_name} not yet executed for checkpoint {checkpoint_id}")
        
        return bool(exists)
    
    async def mark_executed(
        self, 
        checkpoint_id: str, 
        tool_name: str, 
        tool_args: Dict[str, Any],
        result: Optional[Any] = None,
        ttl_seconds: int = 86400  # 24 hours default
    ) -> None:
        """
        Mark a tool execution as completed.
        
        Args:
            checkpoint_id: The checkpoint ID when the tool was called
            tool_name: Name of the tool being executed
            tool_args: Arguments passed to the tool
            result: Optional result to store for recovery
            ttl_seconds: Time-to-live for the idempotency record (default 24h)
        """
        execution_id = self._generate_execution_id(checkpoint_id, tool_name, tool_args)
        
        execution_record = {
            "checkpoint_id": checkpoint_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "result": result,
            "timestamp": None  # Will be set server-side if needed
        }
        
        serialized = StateSerializer.serialize(execution_record)
        await self.redis.setex(execution_id, ttl_seconds, serialized)
        logger.debug(f"Marked tool execution as complete: {tool_name} at checkpoint {checkpoint_id}")
    
    async def get_execution_result(
        self, 
        checkpoint_id: str, 
        tool_name: str, 
        tool_args: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve the stored result from a previous tool execution.
        
        Useful for recovery scenarios where we want to reuse the result.
        
        Args:
            checkpoint_id: The checkpoint ID when the tool was called
            tool_name: Name of the tool being executed
            tool_args: Arguments passed to the tool
            
        Returns:
            Optional[Dict]: Execution record if found, None otherwise
        """
        execution_id = self._generate_execution_id(checkpoint_id, tool_name, tool_args)
        raw_data = await self.redis.get(execution_id)
        
        if not raw_data:
            return None
        
        try:
            return StateSerializer.deserialize(raw_data)
        except ValueError:
            logger.warning(f"Corrupted idempotency record found: {execution_id}")
            return None
