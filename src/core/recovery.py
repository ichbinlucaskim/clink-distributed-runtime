import time
import asyncio
import logging
from typing import List, Dict
from src.core.database import RedisClient
from src.core.serializer import StateSerializer

# 1. Configure Structured Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

class AgentResurrector:
    """
    Service to detect and recover 'Zombie' agents.
    Zombie Definition: Status is 'RUNNING' but 'last_seen' > threshold.
    """
    
    def __init__(self, zombie_threshold_sec: int = 300):
        self.redis = RedisClient.get_client()
        self.threshold = zombie_threshold_sec

    async def scan_zombies(self) -> List[Dict]:
        """
        Scans all heartbeats and identifies dead threads.
        """
        zombies = []
        cursor = b"0"
        current_time = time.time()

        logger.info(f"Scanning for zombies (Threshold: {self.threshold}s)...")

        while cursor:
            cursor, keys = await self.redis.scan(cursor, match="heartbeats:*", count=100)
            
            for key in keys:
                # Robust Key Handling
                if isinstance(key, bytes):
                    key_str = key.decode("utf-8")
                else:
                    key_str = str(key)
                
                raw_data = await self.redis.get(key)
                if not raw_data:
                    continue
                
                try:
                    data = StateSerializer.deserialize(raw_data)
                except ValueError:
                    logger.warning(f"Corrupted heartbeat data found for key: {key_str}")
                    continue

                last_seen = data.get("last_seen", 0)
                status = data.get("status", "UNKNOWN")

                time_diff = current_time - last_seen
                
                if status == "RUNNING" and time_diff > self.threshold:
                    thread_id = key_str.split(":")[-1]
                    zombies.append({
                        "thread_id": thread_id,
                        "inactive_seconds": round(time_diff, 2),
                        "latest_checkpoint": data.get("latest_checkpoint_id"),
                        "last_data": data
                    })
        
        return zombies

    async def resurrect_zombie(self, zombie: Dict):
        """
        [Action] Recover the zombie by marking it as SUSPENDED.
        """
        thread_id = zombie["thread_id"]
        key = f"heartbeats:{thread_id}"
        
        # 1. Update Status
        recovery_data = zombie["last_data"]
        recovery_data["status"] = "SUSPENDED"
        recovery_data["last_seen"] = time.time()
        
        # 2. Persist
        await self.redis.set(key, StateSerializer.serialize(recovery_data))
        
        logger.info(f"Thread {thread_id} recovered. Status updated to SUSPENDED.")

# Production-ready Entry Point
if __name__ == "__main__":
    async def main():
        try:
            RedisClient.get_client()
            service = AgentResurrector(zombie_threshold_sec=5)
            
            zombies = await service.scan_zombies()
            
            if zombies:
                logger.warning(f"Found {len(zombies)} inactive threads. Starting recovery...")
                for z in zombies:
                    logger.info(f"Target: {z['thread_id']} (Inactive: {z['inactive_seconds']}s)")
                    await service.resurrect_zombie(z)
            else:
                logger.info("System healthy. No stalled threads detected.")
                
        except Exception as e:
            logger.error(f"Recovery process failed: {str(e)}")
        finally:
            await RedisClient.close()

    asyncio.run(main())