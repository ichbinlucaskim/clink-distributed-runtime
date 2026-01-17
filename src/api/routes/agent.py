from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
import logging
from src.core.graph import ClinkAgent

logger = logging.getLogger(__name__)

# Prefix is /api/v1/agents
router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# 1. Initialize the Agent (Singleton Pattern recommended for prod, simplified here)
agent_instance = ClinkAgent()

# 2. Define Request Schema
class ChatRequest(BaseModel):
    query: str = Field(..., description="User query to the agent")
    thread_id: str = Field(..., description="Session ID for persistence")

@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """
    [CPE-1] Agent Interaction Endpoint.
    Invokes the Clink LangGraph with Redis persistence.
    
    CPE-9: Includes health probe to ensure Redis connection is ready before execution.
    """
    # Config for Thread-level persistence
    config = {"configurable": {"thread_id": request.thread_id}}
    
    # Resurrection Safety: Health probe before accepting request (CPE-9)
    try:
        # Verify Redis connection is ready (prevent 500 errors post-restart)
        await agent_instance._verify_redis_ready()
        
        # Enhanced 503: Wait until checkpointer is fully multiplexed and ready
        redis = await agent_instance.checkpointer._get_redis_client()
        await redis.ping()  # Additional verification
        
    except Exception as e:
        logger.warning(f"Service unavailable for thread {request.thread_id}: {e}")
        raise HTTPException(
            status_code=503, 
            detail=f"Service Unavailable: Redis connection not ready. {str(e)}"
        )
    
    # Robust Resurrection: State Readiness check before execution
    try:
        await agent_instance._verify_state_readiness(request.thread_id)
    except Exception as e:
        logger.warning(f"State readiness check failed for thread {request.thread_id}: {e}")
        # Non-fatal: continue execution (might be new thread)
    
    # Input for the graph
    inputs = {"messages": [HumanMessage(content=request.query)]}
    
    try:
        # Persistence Guard: Ensure initial checkpoint exists before execution (CPE-8)
        await agent_instance._ensure_initial_checkpoint(request.thread_id)
        
        response_content = "No response generated."
        
        # Stream the graph execution with detailed error logging
        # Wrap in try-except to capture full traceback for debugging
        try:
            async for event in agent_instance.graph.astream(inputs, config=config):
                for key, value in event.items():
                    if "messages" in value:
                        # Get the last message content
                        response_content = value["messages"][-1].content
        except Exception as graph_error:
            # Log full traceback to container logs for debugging
            import traceback
            full_traceback = traceback.format_exc()
            logger.error(f"Graph execution failed for thread {request.thread_id}:\n{full_traceback}")
            raise graph_error  # Re-raise to be caught by outer handler
                    
        return {
            "response": response_content,
            "thread_id": request.thread_id,
            "status": "success"
        }
        
    except Exception as e:
        # Enhanced error handling: Return actual error message for testing
        import traceback
        error_traceback = traceback.format_exc()
        error_message = f"Agent Execution Failed: {str(e)}"
        
        # Log full traceback to container logs
        logger.error(f"Agent execution failed for thread {request.thread_id}:\n{error_traceback}")
        
        # Return detailed error for testing (includes actual error message)
        raise HTTPException(
            status_code=500, 
            detail=error_message
        )

@router.get("/health")
async def health_check():
    return {"status": "agent_service_active"}