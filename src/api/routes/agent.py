from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.get("/")
async def list_agents():
    """List all agents."""
    return {"agents": []}


@router.post("/")
async def create_agent():
    """Create a new agent."""
    return {"message": "Agent creation endpoint"}
