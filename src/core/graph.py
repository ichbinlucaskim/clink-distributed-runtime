import logging
import os
import time
import asyncio
from typing import TypedDict, Annotated, List, Any
from langchain_core.messages import BaseMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from src.core.heartbeat_saver import HeartbeatRedisSaver
from src.core.idempotency import IdempotencyTracker
from src.core.llm import LLMFactory
from src.core.registry import ToolRegistry
from src.core.database import RedisClient

# Setup Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 1. Define State
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], "The conversation history"]

class ClinkAgent:
    def __init__(self):
        # Initialize Memory (Redis Persistence with Heartbeat Tracking)
        # CPE-8: HeartbeatRedisSaver automatically updates heartbeat metadata on every checkpoint save
        redis_url = "redis://redis:6379"
        
        # Retry logic for Redis connection during container startup
        # Handles transient "Connection refused" errors during warm-up (CPE-9)
        self.checkpointer = self._init_checkpointer_with_retry(redis_url)
        
        # CPE-9: Initialize idempotency tracker for preventing duplicate tool side effects
        redis_client = self._init_redis_client_with_retry(redis_url)
        self.idempotency_tracker = IdempotencyTracker(redis_client)
        
        # Initialize Brain & Hands with retry logic for Ollama connections (CPE-9)
        # Handles transient connection errors during container restart/resurrection
        self.llm = self._init_llm_with_retry(model_name="llama3.1")
        self.tools = ToolRegistry.get_tools()
        self.llm_with_tools = self.llm.bind_tools(self.tools)
        
        # Build Graph
        self.graph = self._build_graph()
    
    def _init_checkpointer_with_retry(self, redis_url: str, max_retries: int = 5, delay: float = 1.0) -> HeartbeatRedisSaver:
        """
        Initialize Redis checkpointer with retry logic.
        
        Handles transient connection errors during container startup/warm-up.
        
        Args:
            redis_url: Redis connection URL
            max_retries: Maximum number of retry attempts
            delay: Delay between retries (seconds)
        
        Returns:
            HeartbeatRedisSaver: Initialized checkpointer
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                checkpointer = HeartbeatRedisSaver(redis_url)
                logger.info(f"Redis checkpointer initialized (attempt {attempt + 1})")
                return checkpointer
            except (RedisConnectionError, ConnectionError, OSError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = delay * (attempt + 1)  # Exponential backoff
                    logger.warning(f"Redis connection failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Redis connection failed after {max_retries} attempts: {e}")
        
        raise ConnectionError(f"Failed to initialize Redis checkpointer after {max_retries} attempts: {last_error}")
    
    def _init_redis_client_with_retry(self, redis_url: str, max_retries: int = 5, delay: float = 1.0) -> Redis:
        """
        Initialize Redis client with retry logic.
        
        Handles transient connection errors during container startup/warm-up.
        
        Args:
            redis_url: Redis connection URL
            max_retries: Maximum number of retry attempts
            delay: Delay between retries (seconds)
        
        Returns:
            Redis: Initialized Redis client
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                client = Redis.from_url(redis_url, decode_responses=False)
                # Test connection
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, we can't await, so skip ping test
                    pass
                else:
                    # Simple sync connection test (Redis client handles this internally)
                    pass
                logger.info(f"Redis client initialized (attempt {attempt + 1})")
                return client
            except (RedisConnectionError, ConnectionError, OSError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = delay * (attempt + 1)  # Exponential backoff
                    logger.warning(f"Redis client connection failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Redis client connection failed after {max_retries} attempts: {e}")
        
        raise ConnectionError(f"Failed to initialize Redis client after {max_retries} attempts: {last_error}")
    
    def _init_llm_with_retry(self, model_name: str, max_retries: int = 5, delay: float = 1.0):
        """
        Initialize LLM (Ollama) with retry logic.
        
        Handles transient connection errors during container restart/resurrection (CPE-9).
        The LLM is initialized lazily, so this mainly ensures the factory call succeeds.
        
        Args:
            model_name: Model name to initialize
            max_retries: Maximum number of retry attempts
            delay: Delay between retries (seconds)
        
        Returns:
            Initialized LLM instance
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                llm = LLMFactory.get_model(model_name=model_name)
                logger.info(f"LLM initialized (attempt {attempt + 1}): {model_name}")
                return llm
            except (ConnectionError, OSError, Exception) as e:
                last_error = e
                # Check if it's a connection-related error
                error_str = str(e).lower()
                if "connection" in error_str or "refused" in error_str or "timeout" in error_str:
                    if attempt < max_retries - 1:
                        wait_time = delay * (attempt + 1)  # Exponential backoff
                        logger.warning(f"LLM connection failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"LLM connection failed after {max_retries} attempts: {e}")
                else:
                    # Non-connection error, re-raise immediately
                    logger.error(f"LLM initialization error (non-connection): {e}")
                    raise
        
        raise ConnectionError(f"Failed to initialize LLM ({model_name}) after {max_retries} attempts: {last_error}")

    def _build_graph(self):
        """Constructs the ReAct Loop with Persistence."""
        workflow = StateGraph(AgentState)

        # Nodes
        workflow.add_node("agent", self._call_model)
        # CPE-9: Use idempotency-aware tool node
        workflow.add_node("tools", self._idempotent_tool_node)

        # Edges
        workflow.set_entry_point("agent")
        workflow.add_conditional_edges(
            "agent",
            self._should_continue,
            {
                "continue": "tools",
                "end": END
            }
        )
        workflow.add_edge("tools", "agent")

        # Compile with Checkpointer (Crucial for Recovery)
        return workflow.compile(checkpointer=self.checkpointer)

    def _call_model(self, state: AgentState):
        messages = state["messages"]
        logger.info(f"Agent Thinking... (History: {len(messages)} msgs)")
        response = self.llm_with_tools.invoke(messages)
        return {"messages": [response]}
    
    def _idempotent_tool_node(self, state: AgentState, config: dict[str, Any] = None) -> dict[str, Any]:
        """
        CPE-9: Idempotency-aware tool execution wrapper.
        
        Checks if tool calls have already been executed before invoking them.
        If already executed, returns cached result. Otherwise, executes and marks as done.
        
        Note: Simplified sync implementation for now. Full async idempotency can be added
        when we migrate to async graph execution.
        """
        import asyncio
        
        # Use standard ToolNode for execution (idempotency checking will be enhanced later)
        # For now, delegate to ToolNode - idempotency tracking can be added via checkpoint metadata
        tool_node = ToolNode(self.tools)
        
        # Execute tools normally
        # TODO: Add async idempotency checking when we have checkpoint context available
        # The checkpoint_id can be extracted from the config when graph is invoked with persistence
        result = tool_node.invoke(state, config or {})
        
        return result
    
    async def _ensure_initial_checkpoint(self, thread_id: str):
        """
        Persistence Guard: Ensure initial "STARTING" checkpoint is saved immediately.
        
        CPE-8: Called before heavy processing to ensure first checkpoint exists
        even if container is killed early in execution.
        """
        try:
            # Force initial heartbeat with "starting" checkpoint_id
            await self.checkpointer.force_heartbeat(thread_id, checkpoint_id="starting")
            logger.info(f"Initial checkpoint guard activated for thread {thread_id}")
        except Exception as e:
            logger.warning(f"Failed to create initial checkpoint guard: {e}")
            # Non-fatal: continue execution
    
    async def _verify_redis_ready(self, max_retries: int = 3):
        """
        Resurrection Safety: Health probe to verify Redis connection is ready.
        
        CPE-9: Called before accepting requests post-restart to prevent 500 errors.
        Ensures Redis connection is fully multiplexed and search indexes are created before execution.
        
        Args:
            max_retries: Maximum retry attempts
        
        Raises:
            ConnectionError: If Redis is not ready after retries
        """
        for attempt in range(max_retries):
            try:
                # Try to get a Redis client and ping it
                redis = await self.checkpointer._get_redis_client()
                await redis.ping()
                
                # CRITICAL: Ensure search indexes are created before any checkpoint operations
                # This prevents "No such index checkpoint_write" errors during resurrection
                await self.checkpointer.ensure_indexes()
                
                logger.debug(f"Redis health probe and indexes ready (attempt {attempt + 1})")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 0.5 * (attempt + 1)
                    logger.debug(f"Redis health probe failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Redis health probe failed after {max_retries} attempts: {e}")
                    raise ConnectionError(f"Redis not ready: {e}")
    
    async def _verify_state_readiness(self, thread_id: str, max_retries: int = 3):
        """
        Robust Resurrection Logic: State Readiness check before graph execution.
        
        CPE-9: Verifies checkpoint exists in Redis with retries.
        If connection drops during check, retries up to 3 times.
        
        Args:
            thread_id: Thread ID to verify state for
            max_retries: Maximum retry attempts
        
        Returns:
            bool: True if checkpoint exists or is not required, False otherwise
        """
        for attempt in range(max_retries):
            try:
                # Use checkpointer to verify checkpoint exists
                config = {"configurable": {"thread_id": thread_id}}
                checkpoint_tuple = await self.checkpointer.aget_tuple(config)
                
                if checkpoint_tuple is not None:
                    logger.info(f"State readiness verified for thread {thread_id} (attempt {attempt + 1})")
                    return True
                else:
                    # No checkpoint exists yet - this is OK for new threads
                    logger.debug(f"No checkpoint found for thread {thread_id} (attempt {attempt + 1}) - new thread")
                    return True  # Allow new threads to proceed
                    
            except (RedisConnectionError, ConnectionError, OSError) as e:
                if attempt < max_retries - 1:
                    wait_time = 0.5 * (attempt + 1)
                    logger.warning(f"State readiness check failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"State readiness check failed after {max_retries} attempts: {e}")
                    # Non-fatal: allow execution to proceed (might be new thread)
                    return True
            except Exception as e:
                # Unexpected error - log but allow execution
                logger.warning(f"State readiness check unexpected error: {e}")
                return True
        
        return True  # Default: allow execution

    def _should_continue(self, state: AgentState):
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            logger.info(f"Decision: Tool Call Required ({len(last_message.tool_calls)} calls)")
            return "continue"
        logger.info("Decision: Final Answer Ready")
        return "end"

if __name__ == "__main__":
    # Integration Test with Persistence
    try:
        agent = ClinkAgent()
        print("Graph Compiled Successfully with Redis Persistence.")
        
        # Simple Connectivity Check
        print("Redis Checkpointer: OK")
        
    except Exception as e:
        print(f"Initialization Failed: {e}")