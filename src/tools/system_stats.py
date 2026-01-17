import psutil
import logging
from typing import Dict, Any
from src.core.registry import ToolRegistry

# Configure Logger
logger = logging.getLogger(__name__)

@ToolRegistry.register(
    name="check_system_health", 
    description="Retrieves current system metrics (CPU usage, Memory usage). Use this to check if the environment is healthy before running heavy tasks."
)
def check_system_health() -> Dict[str, Any]:
    """
    Scans the host system to gather vital performance metrics.
    
    Returns:
        dict: Contains 'cpu_percent', 'memory_total_gb', 'memory_used_percent'.
    """
    try:
        # 1. CPU Metrics
        cpu_usage = psutil.cpu_percent(interval=1)
        
        # 2. Memory Metrics
        memory = psutil.virtual_memory()
        total_gb = round(memory.total / (1024 ** 3), 2)
        used_percent = memory.percent
        
        # 3. Construct Report
        health_report = {
            "status": "nominal",
            "cpu_percent": cpu_usage,
            "memory_total_gb": total_gb,
            "memory_used_percent": used_percent,
        }

        # 4. Resource Awareness Logic (Self-Correction Trigger)
        if cpu_usage > 80 or used_percent > 90:
            health_report["status"] = "critical"
            logger.warning(f"High resource usage detected: CPU {cpu_usage}%, MEM {used_percent}%")
        
        return health_report

    except Exception as e:
        logger.error(f"Failed to gather system metrics: {str(e)}")
        raise e


if __name__ == "__main__":
    # Test Execution
    print("--- Tool Registry Verification ---")
    
    # 1. Check Registration
    tools = ToolRegistry.get_tools()
    print(f"Registered Tools Count: {len(tools)}")
    print(f"Tool Name: {tools[0].name}")
    print(f"Tool Schema: {tools[0].args}")
    
    # 2. Execute Tool (via Registry wrapper)
    print("\n--- Execution Result ---")
    result = check_system_health()
    print(f"Result: {result}")