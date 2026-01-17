import orjson
from typing import Any, Dict, Union
from collections import deque

class StateSerializer:
    """
    High-performance JSON serializer using 'orjson'.
    Designed to handle complex agent states before persisting to Redis.
    """

    @staticmethod
    def _default_converter(obj: Any) -> Any:
        """
        Helper to convert non-serializable types to JSON-friendly types.
        Handles:
        - deque (used internally by LangGraph for write queues)
        - set (often used for unique collections)
        """
        if isinstance(obj, deque):
            return list(obj)
        if isinstance(obj, set):
            return list(obj)
        raise TypeError(f"Type {type(obj)} not serializable")

    @staticmethod
    def serialize(data: Any) -> bytes:
        """
        Convert Python object to JSON bytes.
        
        Args:
            data: The agent state or checkpoint data.
            
        Returns:
            bytes: JSON encoded byte string.
        """
        try:
            return orjson.dumps(
                data, 
                default=StateSerializer._default_converter, # Register the converter
                option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY
            )
        except Exception as e:
            raise ValueError(f"Serialization failed: {str(e)}")

    @staticmethod
    def deserialize(data: Union[bytes, str, None]) -> Dict[str, Any]:
        """
        Convert JSON bytes back to Python Dictionary.
        
        Args:
            data: Raw bytes from Redis.
            
        Returns:
            Dict: Reconstructed agent state.
        """
        if not data:
            return {}
        try:
            return orjson.loads(data)
        except Exception as e:
            raise ValueError(f"Deserialization failed: {str(e)}")