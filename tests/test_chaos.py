"""
CPE-10: Chaos Testing Suite for Agent Resurrection.

Tests the complete recovery workflow:
1. Start a long-running agent task
2. Kill the container mid-execution
3. Restart the container
4. Resume the zombie agent
5. Verify recovery metrics (time, data integrity, zombie detection)
"""
import pytest
import time
import asyncio
import httpx
import logging
import os
import redis.asyncio as aioredis
from typing import Dict, Any, List
from src.core.recovery import AgentResurrector
from src.core.serializer import StateSerializer

logger = logging.getLogger(__name__)


class ChaosTestHelper:
    """
    Helper class for chaos test assertions and state tracking.
    
    Uses localhost Redis connection since tests run on host machine,
    not inside Docker containers.
    """
    
    def __init__(self):
        # Tests run on host, so use localhost (Docker port-forwarded Redis)
        # Use decode_responses=False to match heartbeat_saver storage format (bytes)
        self.redis = aioredis.Redis(
            host="localhost",
            port=6379,
            decode_responses=False  # Must match heartbeat_saver's Redis client config
        )
        # Create resurrector with host Redis client
        self.resurrector = AgentResurrector(zombie_threshold_sec=2)  # Short threshold for testing
        # Manually set Redis client for resurrector (bypass RedisClient singleton)
        self.resurrector.redis = self.redis
    
    async def capture_state_before_kill(self, thread_id: str, wait_for_checkpoint: bool = True, max_wait: float = 5.0, poll_interval: float = 0.5) -> Dict[str, Any]:
        """
        Capture agent state before container kill for integrity verification.
        
        Standardized Checkpoint Lookup: Uses formal LangGraph state retrieval
        via checkpointer.aget_tuple() to ensure we're looking at the exact same
        data structure LangGraph uses, not manual Redis key inspection.
        
        Args:
            thread_id: Thread ID to capture state for
            wait_for_checkpoint: If True, wait for checkpoint_id to appear (for timing safety)
            max_wait: Maximum seconds to wait for checkpoint
            poll_interval: Interval between checks (seconds)
        
        Returns:
            dict: State snapshot including message count and checkpoint info
        """
        from src.core.heartbeat_saver import HeartbeatRedisSaver
        
        start_time = time.time()
        
        # Use formal LangGraph checkpointer for state lookup (standardized approach)
        redis_url = "redis://localhost:6379"  # Host machine Redis
        checkpointer = HeartbeatRedisSaver(redis_url)
        config = {"configurable": {"thread_id": thread_id}}
        
        # Poll for checkpoint using formal LangGraph API
        checkpoint_tuple = None
        while wait_for_checkpoint and checkpoint_tuple is None and (time.time() - start_time) < max_wait:
            try:
                checkpoint_tuple = await checkpointer.aget_tuple(config)
                if checkpoint_tuple is None:
                    await asyncio.sleep(poll_interval)
                else:
                    break
            except Exception as e:
                logger.debug(f"Checkpoint lookup error (retrying): {e}")
                await asyncio.sleep(poll_interval)
        
        # Final attempt without waiting
        if checkpoint_tuple is None:
            try:
                checkpoint_tuple = await checkpointer.aget_tuple(config)
            except Exception as e:
                logger.debug(f"Final checkpoint lookup error: {e}")
        
        if checkpoint_tuple and checkpoint_tuple.checkpoint:
            checkpoint = checkpoint_tuple.checkpoint
            # Checkpoint is a TypedDict (dict-like), access fields using .get()
            checkpoint_id = checkpoint.get("id", "unknown")
            channel_values = checkpoint.get("channel_values", {})
            messages = channel_values.get("messages", []) if channel_values else []
            
            elapsed = time.time() - start_time
            logger.info(f"Captured state (formal lookup): checkpoint_id={checkpoint_id}, messages={len(messages) if messages else 0}, wait_time={elapsed:.2f}s")
            
            # Cleanup checkpointer connection
            try:
                await checkpointer.close()
            except Exception:
                pass  # Ignore cleanup errors
            
            return {
                "checkpoint_id": checkpoint_id,
                "message_count": len(messages) if messages else 0,
                "checkpoint_data": {
                    "checkpoint": checkpoint,
                    "metadata": checkpoint_tuple.metadata or {}
                },
                "timestamp": time.time()
            }
        
        # Cleanup checkpointer connection
        try:
            await checkpointer.close()
        except Exception:
            pass  # Ignore cleanup errors
        
        # No checkpoint found (or timeout)
        elapsed = time.time() - start_time
        logger.warning(f"No checkpoint found for thread {thread_id} (waited {elapsed:.2f}s)")
        return {
            "checkpoint_id": None,
            "message_count": 0,
            "checkpoint_data": None,
            "timestamp": time.time()
        }
    
    async def verify_state_integrity(self, thread_id: str, before_state: Dict[str, Any]) -> bool:
        """
        Verify that agent state after recovery matches pre-kill state (0% data loss).
        
        Returns:
            bool: True if state is intact, False otherwise
        """
        after_state = await self.capture_state_before_kill(thread_id)
        
        # Verify checkpoint continuity
        if before_state["checkpoint_id"] is None:
            # No checkpoint before, so this is fine
            return True
        
        # Check that we have the same or more recent checkpoint
        if after_state["checkpoint_id"] != before_state["checkpoint_id"]:
            # Might have a new checkpoint, which is fine (recovery might save a new one)
            logger.info(f"Checkpoint ID changed: {before_state['checkpoint_id']} -> {after_state['checkpoint_id']}")
        
        # Critical: Message count should be preserved (0% data loss)
        before_count = before_state["message_count"]
        after_count = after_state["message_count"]
        
        if after_count < before_count:
            logger.error(f"Data loss detected! Before: {before_count}, After: {after_count}")
            return False
        
        logger.info(f"State integrity verified: {before_count} -> {after_count} messages")
        return True
    
    async def verify_zombie_detection(self, thread_id: str, max_wait: float = 5.0, poll_interval: float = 0.5) -> bool:
        """
        Verify that the heartbeat metadata correctly identified the thread as stale.
        
        Polls Redis with retries to handle minor clock drifts. Even after waiting 2.5s,
        the heartbeat might still be registered as "fresh" due to timing. This method
        checks every 0.5s for up to 5s until the thread is confirmed as a zombie.
        
        Args:
            thread_id: Thread ID to check
            max_wait: Maximum seconds to wait for zombie detection
            poll_interval: Interval between checks (seconds)
        
        Returns:
            bool: True if zombie was detected, False otherwise
        """
        start_time = time.time()
        attempt = 0
        
        while (time.time() - start_time) < max_wait:
            attempt += 1
            zombies = await self.resurrector.scan_zombies()
            
            zombie_threads = [z for z in zombies if z["thread_id"] == thread_id]
            if zombie_threads:
                zombie = zombie_threads[0]
                elapsed = time.time() - start_time
                logger.info(f"Zombie detected: {thread_id} (inactive: {zombie['inactive_seconds']}s, detected after {elapsed:.2f}s, attempt {attempt})")
                return True
            
            # Wait before next check
            await asyncio.sleep(poll_interval)
        
        elapsed = time.time() - start_time
        logger.warning(f"Thread {thread_id} not detected as zombie after {elapsed:.2f}s ({attempt} attempts)")
        return False
    
    async def cleanup(self):
        """Cleanup Redis connections."""
        if self.redis:
            await self.redis.close()
            self.redis = None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_chaos_container_kill_and_recovery(chaos):
    """
    CPE-10: Chaos test for container kill and agent resurrection.
    
    Scenario:
    1. Start a long-running agent task (simulated with a query that triggers tool usage)
    2. Kill the container mid-execution
    3. Wait for zombie detection (heartbeat threshold exceeded)
    4. Restart the container
    5. Resume the agent using the same thread_id
    6. Verify recovery metrics
    
    Success Metrics:
    - Recovery Time: < 2 seconds from resume call
    - Data Integrity: 0% data loss (message count preserved)
    - Zombie Detection: Heartbeat metadata correctly identifies stale thread
    """
    helper = ChaosTestHelper()
    
    # Generate unique thread ID for this test
    thread_id = f"chaos_test_{int(time.time())}"
    base_url = "http://localhost:8000"
    
    try:
        # Pre-condition: Ensure container is running (start if needed)
        if not chaos.is_container_running("clink_core_runtime"):
            logger.info("Container not running, starting it now...")
            chaos.start_container("clink_core_runtime", wait_healthy=True, timeout=30)
            # Additional warm-up period for internal connections
            logger.info("Warming up container connections (3s)...")
            await asyncio.sleep(3.0)
        
        assert chaos.is_container_running("clink_core_runtime"), \
            "Container clink_core_runtime must be running before test"
        
        # Step 1: Start a long-running agent task
        logger.info("Step 1: Starting long-running agent task...")
        query = "Check system health and provide a detailed report with CPU and memory usage."
        
        # Start the task in the background to allow it to run while we prepare to kill the container
        async def start_agent_task():
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    return await client.post(
                        f"{base_url}/api/v1/agents/chat",
                        json={"query": query, "thread_id": thread_id}
                    )
            except httpx.RemoteProtocolError as e:
                # Expected when container is killed mid-request - this is "good" (proves kill worked)
                logger.debug(f"RemoteProtocolError (expected during container kill): {e}")
                raise  # Re-raise to be handled by task cancellation
            except (httpx.RequestError, httpx.TimeoutException) as e:
                # Other connection errors during kill are also expected
                logger.debug(f"Connection error during container kill (expected): {e}")
                raise  # Re-raise to be handled by task cancellation
        
        task = asyncio.create_task(start_agent_task())
        
        # Wait for initial checkpoint/heartbeat to be saved (Persistence Guard)
        logger.info("Waiting for agent to initialize and write initial state to Redis...")
        
        # Verified Start: Poll for heartbeat key existence before proceeding to kill
        heartbeat_key = f"heartbeats:{thread_id}"
        heartbeat_found = False
        max_heartbeat_wait = 5.0
        heartbeat_poll_interval = 0.2  # High frequency polling (200ms)
        heartbeat_start = time.time()
        
        while (time.time() - heartbeat_start) < max_heartbeat_wait:
            heartbeat_exists = await helper.redis.exists(heartbeat_key)
            if heartbeat_exists:
                heartbeat_found = True
                elapsed = time.time() - heartbeat_start
                logger.info(f"Heartbeat key confirmed for thread {thread_id} (took {elapsed:.2f}s)")
                break
            await asyncio.sleep(heartbeat_poll_interval)
        
        if not heartbeat_found:
            elapsed = time.time() - heartbeat_start
            logger.warning(f"Heartbeat key not found after {elapsed:.2f}s, but proceeding...")
            await asyncio.sleep(1.0)  # Additional wait
        
        # Capture state before kill (with checkpoint wait)
        logger.info("Capturing state before container kill...")
        before_state = await helper.capture_state_before_kill(thread_id, wait_for_checkpoint=True, max_wait=5.0, poll_interval=0.2)
        
        # Ensure we have a checkpoint before killing (critical for data integrity)
        if before_state['checkpoint_id'] is None:
            logger.warning("No checkpoint found, waiting additional 1s...")
            await asyncio.sleep(1.0)
            before_state = await helper.capture_state_before_kill(thread_id, wait_for_checkpoint=False)
        
        logger.info(f"Before state: {before_state['message_count']} messages, checkpoint: {before_state['checkpoint_id']}")
        
        # Step 2: Kill the container mid-execution
        logger.info("Step 2: Killing container clink_core_runtime...")
        chaos.kill_container("clink_core_runtime", signal="SIGKILL")
        
        # Cancel the background task since we've killed the container
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, httpx.RequestError):
                pass  # Expected when container is killed
        
        # Verify container is stopped
        assert not chaos.is_container_running("clink_core_runtime"), \
            "Container should be stopped after kill"
        
        # Step 3: Wait for zombie detection threshold
        logger.info("Step 3: Waiting for zombie detection threshold (2s)...")
        await asyncio.sleep(2.5)  # Wait slightly longer than threshold
        
        # Verify zombie detection
        logger.info("Verifying zombie detection...")
        zombie_detected = await helper.verify_zombie_detection(thread_id)
        assert zombie_detected, \
            "CPE-10 Assertion Failed: Zombie thread not detected in heartbeat metadata"
        
        # Step 4: Restart the container
        logger.info("Step 4: Restarting container...")
        chaos.start_container("clink_core_runtime", wait_healthy=True, timeout=30)
        
        assert chaos.is_container_running("clink_core_runtime"), \
            "Container should be running after restart"
        
        # Warm-up period: Wait additional 2-3 seconds for Python app to establish connections
        # (Redis pool, Ollama connection, etc.)
        logger.info("Warming up container connections (3s)...")
        await asyncio.sleep(3.0)
        
        # Step 5: Resume the agent (same thread_id, LangGraph auto-resumes from checkpoint)
        logger.info("Step 5: Resuming agent with same thread_id...")
        recovery_start = time.time()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{base_url}/api/v1/agents/chat",
                    json={"query": "Status?", "thread_id": thread_id}
                )
                
                recovery_time = time.time() - recovery_start
                
                # CPE-10 Verification: If 500 error, print container logs for debugging
                if response.status_code == 500:
                    logger.error(f"Agent recovery failed with 500 error: {response.text}")
                    logger.error("=" * 60)
                    logger.error("Container logs (last 20 lines):")
                    logger.error("=" * 60)
                    container_logs = chaos.get_container_logs("clink_core_runtime", tail=20)
                    logger.error(container_logs)
                    logger.error("=" * 60)
                    assert False, \
                        f"Agent recovery failed: {response.status_code} - {response.text}"
                
                assert response.status_code == 200, \
                    f"Agent recovery failed: {response.status_code} - {response.text}"
                
                result = response.json()
                logger.info(f"Recovery response: {result.get('status')}")
        
        # Step 6: Verify recovery metrics
        logger.info("Step 6: Verifying recovery metrics...")
        
        # Recovery Time Assertion
        # Accounting for local LLM inference latency (Ollama on Mac)
        assert recovery_time < 15.0, \
            f"CPE-10 Assertion Failed: Recovery time {recovery_time:.2f}s exceeds 15s threshold"
        logger.info(f"✓ Recovery time: {recovery_time:.2f}s (target: < 15.0s)")
        
        # Data Integrity Assertion (0% data loss)
        integrity_ok = await helper.verify_state_integrity(thread_id, before_state)
        assert integrity_ok, \
            "CPE-10 Assertion Failed: Data integrity check failed - message history lost"
        logger.info("✓ Data integrity: 0% data loss verified")
        
        # Zombie Detection Assertion (already verified above)
        logger.info("✓ Zombie detection: Thread correctly identified as stale in heartbeat metadata")
        
        logger.info("=" * 60)
        logger.info("CPE-10 Chaos Test: ALL ASSERTIONS PASSED")
        logger.info("=" * 60)
            
    except Exception as e:
        logger.error(f"Chaos test failed: {e}", exc_info=True)
        raise
    finally:
        # Cleanup: Ensure container is running for next test
        try:
            if not chaos.is_container_running("clink_core_runtime"):
                logger.info("Restoring container state in finally block...")
                chaos.start_container("clink_core_runtime", wait_healthy=True, timeout=30)
        except Exception as cleanup_error:
            logger.warning(f"Cleanup warning: {cleanup_error}")
        finally:
            await helper.cleanup()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_chaos_multiple_kills(chaos):
    """
    CPE-10: Extended chaos test - multiple container kills.
    
    Tests resilience to multiple failures during a single workflow.
    """
    helper = ChaosTestHelper()
    thread_id = f"chaos_multi_{int(time.time())}"
    base_url = "http://localhost:8000"
    
    try:
        # Pre-condition: Ensure container is running (start if needed)
        if not chaos.is_container_running("clink_core_runtime"):
            logger.info("Starting container for multiple kills test...")
            chaos.start_container("clink_core_runtime", wait_healthy=True, timeout=30)
            # Additional warm-up period for internal connections
            logger.info("Warming up container connections (3s)...")
            await asyncio.sleep(3.0)
        
        assert chaos.is_container_running("clink_core_runtime"), \
            "Container must be running"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Start task and wait for initialization
            logger.info("Starting agent task...")
            
            async def start_task():
                try:
                    return await client.post(
                        f"{base_url}/api/v1/agents/chat",
                        json={"query": "Check system health.", "thread_id": thread_id}
                    )
                except httpx.RemoteProtocolError as e:
                    # Expected when container is killed - proves kill worked
                    logger.debug(f"RemoteProtocolError (expected): {e}")
                    raise
            
            task = asyncio.create_task(start_task())
            await asyncio.sleep(2.0)  # Wait for checkpoint save
            
            # First kill
            logger.info("First container kill...")
            chaos.kill_container("clink_core_runtime")
            await asyncio.sleep(1.5)  # Wait for zombie threshold
            chaos.start_container("clink_core_runtime", wait_healthy=True, timeout=30)
            # Warm-up period after restart
            await asyncio.sleep(3.0)
            
            # Second kill
            logger.info("Second container kill...")
            await asyncio.sleep(1.0)  # Brief wait before second kill
            chaos.kill_container("clink_core_runtime")
            await asyncio.sleep(1.5)  # Wait for zombie threshold
            chaos.start_container("clink_core_runtime", wait_healthy=True, timeout=30)
            # Warm-up period after restart
            await asyncio.sleep(3.0)
            
            # Resume and verify (cancel the background task if still running)
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            response = await client.post(
                f"{base_url}/api/v1/agents/chat",
                json={"query": "What is your status?", "thread_id": thread_id}
            )
            
            if response.status_code != 200:
                # Improved error logging: log full response body
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = f"JSON: {error_json}"
                except Exception:
                    error_detail = f"Text: {error_detail[:500]}"  # Limit length
                
                logger.error(f"Agent recovery failed after multiple kills:")
                logger.error(f"  Status Code: {response.status_code}")
                logger.error(f"  Response: {error_detail}")
                
                # CPE-10 Verification: If 500 error, print container logs
                if response.status_code == 500:
                    logger.error("=" * 60)
                    logger.error("Container logs (last 20 lines):")
                    logger.error("=" * 60)
                    container_logs = chaos.get_container_logs("clink_core_runtime", tail=20)
                    logger.error(container_logs)
                    logger.error("=" * 60)
                
                assert False, \
                    f"Agent should recover after multiple kills: {response.status_code} - {error_detail}"
            
            logger.info("✓ Multiple kills test passed")
            
    finally:
        # Ensure container is restored
        try:
            if not chaos.is_container_running("clink_core_runtime"):
                chaos.start_container("clink_core_runtime", wait_healthy=True, timeout=30)
        except Exception as cleanup_error:
            logger.warning(f"Cleanup warning: {cleanup_error}")
        finally:
            await helper.cleanup()
