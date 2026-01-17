import inspect
import logging
import functools
from typing import Callable, Dict, Any, List
from pydantic import BaseModel, create_model
from langchain_core.tools import StructuredTool

# Setup Logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

class ToolRegistry:
    """
    Central Repository for Agent Tools.
    Features:
    - Auto-schema generation for LLMs.
    - Error handling wrapper (prevents agent crash on tool failure).
    - Middleware support (logging, metrics).
    """
    _registry: Dict[str, StructuredTool] = {}

    @classmethod
    def register(cls, name: str = None, description: str = None):
        """
        Decorator to register a function as an agent tool.
        """
        def decorator(func: Callable):
            tool_name = name or func.__name__
            tool_desc = description or func.__doc__ or "No description provided."
            
            # 1. Pydantic Model Creation (Auto-Schema)
            args_schema = cls._create_args_schema(func)

            # 2. Wrap with Safety Logic
            @functools.wraps(func)
            def safe_wrapper(*args, **kwargs):
                try:
                    logger.info(f"Tool Execution Started: {tool_name}")
                    result = func(*args, **kwargs)
                    logger.info(f"Tool Execution Success: {tool_name}")
                    return result
                except Exception as e:
                    logger.error(f"Tool Execution Failed: {tool_name} - {str(e)}")
                    # Return error as string so the Agent knows it failed (instead of crashing)
                    return f"Error executing tool {tool_name}: {str(e)}"

            # 3. Create LangChain StructuredTool
            tool_instance = StructuredTool.from_function(
                func=safe_wrapper,
                name=tool_name,
                description=tool_desc,
                args_schema=args_schema
            )
            
            # 4. Register
            cls._registry[tool_name] = tool_instance
            return safe_wrapper
        return decorator

    @classmethod
    def get_tools(cls) -> List[StructuredTool]:
        """Return list of all registered tools for the Agent."""
        return list(cls._registry.values())

    @staticmethod
    def _create_args_schema(func: Callable) -> type[BaseModel]:
        """
        Dynamically create Pydantic model from function signature.
        """
        sig = inspect.signature(func)
        fields = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self": continue
            
            annotation = param.annotation if param.annotation != inspect.Parameter.empty else Any
            default = param.default if param.default != inspect.Parameter.empty else ...
            fields[param_name] = (annotation, default)
        
        return create_model(f"{func.__name__}Schema", **fields)

# Example Usage for verification
if __name__ == "__main__":
    
    @ToolRegistry.register(name="calculator", description="Useful for math operations.")
    def add(a: int, b: int) -> int:
        """Adds two numbers."""
        return a + b

    print(f"Registered Tools: {list(ToolRegistry._registry.keys())}")
    
    # Verify Schema Generation
    schema = ToolRegistry._registry["calculator"].args
    print(f"Schema: {schema}")