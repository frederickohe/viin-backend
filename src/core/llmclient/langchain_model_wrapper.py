"""LangChain Model Wrapper for AutoBus

This module provides a LangChain-compatible wrapper around Groq's OpenAI-compatible API,
integrating with the existing LLMClient while supporting LangChain's agent and tool framework.
"""

import logging
from typing import Dict, List, Any, Optional
from langchain_openai import ChatOpenAI
from langchain.schema import BaseMessage, HumanMessage, SystemMessage, AIMessage
import os
from dotenv import load_dotenv
from core.nlu.config import GROQ_API_KEY, GROQ_BASE_URL, MODEL

logger = logging.getLogger(__name__)

# Load environment variables
env_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env')
load_dotenv(env_path)


class LangChainOpenAIWrapper:
    """
    LangChain-compatible wrapper for Groq-hosted chat models.
    
    This wrapper provides a consistent interface for use with LangChain agents and tools
    while maintaining compatibility with the existing LLMClient infrastructure.
    """
    
    def __init__(
        self,
        model_name: str = MODEL,
        temperature: float = 0.5,
        max_tokens: int = 2096,
        api_key: Optional[str] = None
    ):
        """
        Initialize the LangChain Groq wrapper.
        
        Args:
            model_name: Groq model name
            temperature: Creativity level (0-1), default 0.5
            max_tokens: Maximum tokens for responses, default 2096
            api_key: Groq API key (defaults to GROQ_API_KEY env var)
        """
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        
        # Use provided API key or get from environment
        self.api_key = api_key or GROQ_API_KEY or os.getenv("GROQ_API_KEY")
        
        # Initialize LangChain via Groq's OpenAI-compatible endpoint
        self.llm = ChatOpenAI(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=self.api_key,
            base_url=GROQ_BASE_URL,
            request_timeout=120
        )
        
        logger.info(f"Initialized LangChain Groq wrapper with model: {model_name}")
    
    def __call__(self, prompt: str, **kwargs) -> str:
        """
        Process a prompt using Groq.
        
        Args:
            prompt: The input prompt/message
            **kwargs: Additional arguments (temperature, max_tokens, etc.)
            
        Returns:
            Model's response as string
        """
        return self.invoke(prompt, **kwargs)
    
    def invoke(self, prompt: str, **kwargs) -> str:
        """
        Invoke the model with a text prompt.
        
        Args:
            prompt: The input prompt
            **kwargs: Additional arguments
            
        Returns:
            Model's response as string
        """
        try:
            # Create a message
            messages = [HumanMessage(content=prompt)]
            
            # Get response
            response = self.llm(messages)
            
            # Return the content as string
            return response.content
        except Exception as e:
            logger.error(f"Error invoking LLM: {e}", exc_info=True)
            raise
    
    def invoke_with_messages(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Process messages in chat format.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt
            **kwargs: Additional arguments
            
        Returns:
            Model's response as string
        """
        try:
            # Build message list
            langchain_messages = []
            
            # Add system prompt if provided
            if system_prompt:
                langchain_messages.append(SystemMessage(content=system_prompt))
            
            # Add conversation messages
            for msg in messages:
                role = msg.get("role", "user").lower()
                content = msg.get("content", "")
                
                if role == "system":
                    langchain_messages.append(SystemMessage(content=content))
                elif role == "assistant":
                    langchain_messages.append(AIMessage(content=content))
                else:  # user or default
                    langchain_messages.append(HumanMessage(content=content))
            
            # Get response
            response = self.llm(langchain_messages)
            
            # Return content as string
            return response.content
        except Exception as e:
            logger.error(f"Error invoking LLM with messages: {e}", exc_info=True)
            raise
    
    def get_langchain_llm(self):
        """
        Get the underlying LangChain ChatOpenAI instance.
        
        Returns:
            ChatOpenAI instance for use with LangChain agents and tools
        """
        return self.llm
