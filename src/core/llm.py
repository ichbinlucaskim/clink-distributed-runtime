import os
import logging
from langchain_ollama import ChatOllama
from langchain_core.language_models import BaseChatModel

# Configure Logger
logger = logging.getLogger(__name__)

class LLMFactory:
    """
    Factory class for Local-First LLM (Ollama).
    """

    @staticmethod
    def get_model(model_name: str = "llama3.1:latest", temperature: float = 0) -> BaseChatModel:
        # Default to localhost (for local run), but allow override via env var (for Docker)
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

        logger.info(f"Initializing Local LLM: {model_name} via {base_url}")
        
        return ChatOllama(
            model=model_name,
            temperature=temperature,
            base_url=base_url,
            keep_alive="5m", 
        )