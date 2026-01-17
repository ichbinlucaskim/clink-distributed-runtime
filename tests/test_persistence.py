import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest
from typing import TypedDict
from langgraph.graph import StateGraph, END
from src.core.checkpointer import RedisSaver

# 1. Define a simple State for testing
class AgentState(TypedDict):
    count: int
    messages: list[str]

# 2. Define simple nodes (Agent Actions)
def step_one(state: AgentState):
    """Increment count and add a message."""
    return {
        "count": state["count"] + 1,
        "messages": state["messages"] + ["Step 1 Executed"]
    }

def step_two(state: AgentState):
    """Increment count again."""
    return {
        "count": state["count"] + 1,
        "messages": state["messages"] + ["Step 2 Executed"]
    }

@pytest.mark.asyncio
async def test_redis_persistence_workflow():
    """
    [CPE-7] Fault Tolerance Verification.
    Scenario:
    1. Run a workflow with a specific thread_id.
    2. Verify that the state is saved in Redis.
    3. 'Revive' a new checkpointer and load the state to verify persistence.
    """
    
    # --- Setup: Define the Graph ---
    workflow = StateGraph(AgentState)
    workflow.add_node("step_1", step_one)
    workflow.add_node("step_2", step_two)
    workflow.set_entry_point("step_1")
    workflow.add_edge("step_1", "step_2")
    workflow.add_edge("step_2", END)

    # Use our Custom Redis Saver
    async with RedisSaver.from_conn_info() as checkpointer:
        app = workflow.compile(checkpointer=checkpointer)

        # --- Phase 1: Execution ---
        thread_id = "test-thread-zombie-001"
        config = {"configurable": {"thread_id": thread_id}}
        
        initial_input = {"count": 0, "messages": ["Start"]}
        
        # Run the graph
        # This simulates an agent working and finishing a task
        result = await app.ainvoke(initial_input, config)

        print(f"\n[INFO] Workflow Result: {result}")
        
        # Assertion 1: Logic correctness
        assert result["count"] == 2
        assert "Step 2 Executed" in result["messages"]

        # --- Phase 2: Persistence Verification (The Zombie Check) ---
        # Now we manually fetch the state from Redis using the checkpointer
        # avoiding the memory cache to prove it's in the DB.
        
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        
        assert checkpoint_tuple is not None, "Failed to load checkpoint from Redis!"
        
        saved_state = checkpoint_tuple.checkpoint["channel_values"]
        
        print(f"[INFO] Persisted State from Redis: {saved_state}")

        # Assertion 2: Data Integrity
        # Does the Redis brain remember exactly what happened?
        assert saved_state["count"] == 2
        assert saved_state["messages"][0] == "Start"
        assert saved_state["messages"][-1] == "Step 2 Executed"

    print("\n✅ [SUCCESS] Agent state successfully persisted and revived from Redis.")