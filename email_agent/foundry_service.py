"""Foundry Local Singleton Service.

This module provides a singleton pattern for managing Foundry Local connections,
ensuring the model is loaded once and reused across all requests.

Usage:
    from email_agent.foundry_service import get_foundry_llm, get_foundry_endpoint
    
    # Get the shared LLM instance
    llm = get_foundry_llm()
    
    # Or get just the endpoint for custom configuration
    endpoint, api_key = get_foundry_endpoint()
"""

import logging
import os
import threading
from typing import Optional, Tuple

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


class FoundryService:
    """Singleton service for managing Foundry Local connections.
    
    This ensures the model is loaded only once and the connection
    persists across all requests during the application lifecycle.
    """
    
    _instance: Optional["FoundryService"] = None
    _lock = threading.Lock()
    _initialized = False
    
    # Configuration
    DEFAULT_MODEL = "Phi-4-generic-gpu"
    DEFAULT_TEMPERATURE = 0.0
    
    def __new__(cls) -> "FoundryService":
        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the Foundry service (only runs once due to singleton)."""
        if self._initialized:
            return
            
        with self._lock:
            if self._initialized:
                return
                
            self._manager = None
            self._llm = None
            self._endpoint = None
            self._api_key = None
            self._model_name = os.getenv("FOUNDRY_MODEL", self.DEFAULT_MODEL)
            
            self._initialized = True
    
    def _ensure_initialized(self) -> None:
        """Lazy initialization of Foundry Local manager."""
        if self._manager is not None:
            return
            
        with self._lock:
            if self._manager is not None:
                return
                
            logger.info(f"🚀 Initializing Foundry Local with {self._model_name}...")
            
            try:
                from foundry_local import FoundryLocalManager
                self._manager = FoundryLocalManager(self._model_name)
                self._endpoint = self._manager.endpoint
                self._api_key = self._manager.api_key
                logger.info(f"✅ Foundry endpoint: {self._endpoint}")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Foundry Local: {e}")
                raise
    
    @property
    def endpoint(self) -> str:
        """Get the Foundry Local endpoint URL."""
        self._ensure_initialized()
        return self._endpoint
    
    @property
    def api_key(self) -> str:
        """Get the Foundry Local API key."""
        self._ensure_initialized()
        return self._api_key
    
    @property
    def model_name(self) -> str:
        """Get the model name."""
        return self._model_name
    
    def get_llm(self, temperature: float = None) -> ChatOpenAI:
        """Get a ChatOpenAI instance configured for Foundry Local.
        
        Args:
            temperature: Optional temperature override (default: 0.0)
            
        Returns:
            ChatOpenAI instance connected to Foundry Local
        """
        self._ensure_initialized()
        
        temp = temperature if temperature is not None else self.DEFAULT_TEMPERATURE
        
        # Cache the default LLM instance
        if temp == self.DEFAULT_TEMPERATURE and self._llm is not None:
            return self._llm
        
        llm = ChatOpenAI(
            base_url=self._endpoint,
            api_key=self._api_key,
            model=self._model_name,
            temperature=temp
        )
        
        # Cache only the default temperature LLM
        if temp == self.DEFAULT_TEMPERATURE:
            self._llm = llm
        
        return llm
    
    def is_ready(self) -> bool:
        """Check if the Foundry service is ready."""
        return self._manager is not None
    
    def health_check(self) -> dict:
        """Perform a health check on the Foundry service.
        
        Returns:
            dict with status information
        """
        try:
            self._ensure_initialized()
            return {
                "status": "healthy",
                "endpoint": self._endpoint,
                "model": self._model_name,
                "ready": True
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "ready": False
            }


# Module-level convenience functions
_service: Optional[FoundryService] = None


def get_foundry_service() -> FoundryService:
    """Get the singleton Foundry service instance."""
    global _service
    if _service is None:
        _service = FoundryService()
    return _service


def get_foundry_llm(temperature: float = None) -> ChatOpenAI:
    """Get a ChatOpenAI instance configured for Foundry Local.
    
    This is the recommended way to get an LLM instance. The underlying
    Foundry Local connection is managed as a singleton.
    
    Args:
        temperature: Optional temperature override (default: 0.0)
        
    Returns:
        ChatOpenAI instance connected to Foundry Local
        
    Example:
        llm = get_foundry_llm()
        response = llm.invoke("Hello!")
    """
    return get_foundry_service().get_llm(temperature)


def get_foundry_endpoint() -> Tuple[str, str]:
    """Get the Foundry Local endpoint and API key.
    
    Returns:
        Tuple of (endpoint_url, api_key)
        
    Example:
        endpoint, api_key = get_foundry_endpoint()
        llm = ChatOpenAI(base_url=endpoint, api_key=api_key, model="Phi-4-generic-gpu")
    """
    service = get_foundry_service()
    return service.endpoint, service.api_key


def foundry_health_check() -> dict:
    """Check the health of the Foundry Local service.
    
    Returns:
        dict with status, endpoint, model, and ready fields
    """
    return get_foundry_service().health_check()
