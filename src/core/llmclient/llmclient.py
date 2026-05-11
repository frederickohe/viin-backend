import openai
import base64
import logging
from typing import Dict, List, Any, Optional
from core.nlu.config import (
    AUDIO_TRANSCRIPTION_MODEL,
    GROQ_API_KEY,
    GROQ_BASE_URL,
    MODEL,
    MODEL_CONFIG,
)

logger = logging.getLogger(__name__)


class LLMClient:
    """Centralized LLM API client for handling all LLM conversations, including multimodal inputs"""
    
    def __init__(self, model: Optional[str] = None):
        self.client = openai.OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
        self.model = model or MODEL
    
    def chat_completion(
        self,
        system_prompt: str,
        user_message: str,
        conversation_history: Optional[List[Dict]] = None,
        temperature: float = 0.1,
        max_tokens: int = 500,
        image_url: Optional[str] = None,
        image_base64: Optional[str] = None,
        image_media_type: str = "image/jpeg"
    ) -> str:
        """
        Generic method for LLM chat completions with optional image support (vision)
        
        Args:
            system_prompt: The system prompt/instruction
            user_message: The current user message
            conversation_history: Previous conversation messages
            temperature: Creativity level (0-1)
            max_tokens: Maximum response length
            image_url: URL to an image (for vision capabilities)
            image_base64: Base64-encoded image data
            image_media_type: MIME type of the image (image/jpeg, image/png, image/gif, image/webp)
            
        Returns:
            LLM response as string
        """
        messages = self._build_messages(
            system_prompt,
            user_message,
            conversation_history,
            image_url=image_url,
            image_base64=image_base64,
            image_media_type=image_media_type,
        )

        try:
            logger.debug("Sending Groq chat completion request: model=%s", self.model)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_tokens,
            )

            message = response.choices[0].message if response.choices else None
            content = getattr(message, "content", "") if message else ""
            return content.strip() if isinstance(content, str) else str(content).strip()

        except Exception as e:
            logger.error(f"Error in LLM API call: {e}")
            return ""
    
    def _build_messages(
        self,
        system_prompt: str,
        user_message: str,
        conversation_history: Optional[List[Dict]] = None,
        image_url: Optional[str] = None,
        image_base64: Optional[str] = None,
        image_media_type: str = "image/jpeg"
    ) -> List[Dict]:
        """Build messages for Groq's OpenAI-compatible chat completions API."""
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if conversation_history:
            for msg in conversation_history[-6:]:
                role = msg.get("role", "user")
                if role not in {"system", "assistant", "user"}:
                    role = "user"
                messages.append({"role": role, "content": msg.get("content", "")})
        
        if image_url or image_base64:
            user_content: List[Dict[str, Any]] = [
                {
                    "type": "text",
                    "text": user_message
                }
            ]

            if image_base64:
                data_url = f"data:{image_media_type};base64,{image_base64}"
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": data_url},
                })
            elif image_url:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": image_url},
                })

            messages.append({"role": "user", "content": user_content})
            return messages

        messages.append({"role": "user", "content": user_message})
        return messages
    
    def structured_completion(
        self,
        system_prompt: str,
        user_message: str,
        expected_format: str,
        conversation_history: Optional[List[Dict]] = None
    ) -> str:
        """
        For structured outputs where you expect specific format
        
        Args:
            expected_format: Description of expected output format
        """
        enhanced_prompt = f"{user_message}\n\nPlease respond in this format:\n{expected_format}"
        
        return self.chat_completion(
            system_prompt=system_prompt,
            user_message=enhanced_prompt,
            conversation_history=conversation_history,
            temperature=0.1  # Lower temperature for structured outputs
        )
    
    def transcribe_audio(self, audio_file_path: str) -> Optional[str]:
        """
        Transcribe audio file to text using Groq's transcription API
        
        Args:
            audio_file_path: Path to the audio file or file object
            
        Returns:
            Transcribed text or None if transcription fails
        """
        try:
            logger.info(f"Transcribing audio file: {audio_file_path}")
            
            with open(audio_file_path, "rb") as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model=AUDIO_TRANSCRIPTION_MODEL,
                    file=audio_file,
                    language="en"  # Specify English; adjust as needed for multilingual support
                )
            
            transcribed_text = transcript.text
            logger.info(f"Audio transcription successful: {transcribed_text}")
            return transcribed_text
            
        except Exception as e:
            logger.error(f"Error transcribing audio: {e}")
            return None
    
    def transcribe_audio_from_bytes(self, audio_bytes: bytes, filename: str = "audio.mp3") -> Optional[str]:
        """
        Transcribe audio from bytes using Groq's transcription API
        
        Args:
            audio_bytes: Audio file content as bytes
            filename: Filename with extension (for format detection)
            
        Returns:
            Transcribed text or None if transcription fails
        """
        try:
            logger.info(f"Transcribing audio from bytes: {filename}")
            
            from io import BytesIO
            audio_file = BytesIO(audio_bytes)
            audio_file.name = filename
            
            transcript = self.client.audio.transcriptions.create(
                model=AUDIO_TRANSCRIPTION_MODEL,
                file=audio_file,
                language="en"  # Specify English; adjust as needed
            )
            
            transcribed_text = transcript.text
            logger.info(f"Audio transcription successful: {transcribed_text}")
            return transcribed_text
            
        except Exception as e:
            logger.error(f"Error transcribing audio from bytes: {e}")
            return None
    
    def chat_completion_with_audio(
        self,
        system_prompt: str,
        user_message: str,
        conversation_history: Optional[List[Dict]] = None,
        temperature: float = 0.1,
        max_tokens: int = 500,
        audio_file_path: Optional[str] = None,
        audio_bytes: Optional[bytes] = None,
        audio_filename: str = "audio.mp3"
    ) -> str:
        """
        Process user message with optional audio transcription
        
        Args:
            system_prompt: The system prompt/instruction
            user_message: The current user message
            conversation_history: Previous conversation messages
            temperature: Creativity level (0-1)
            max_tokens: Maximum response length
            audio_file_path: Path to audio file to transcribe
            audio_bytes: Audio file content as bytes
            audio_filename: Filename for audio bytes (default: audio.mp3)
            
        Returns:
            LLM response as string
        """
        # Transcribe audio if provided
        audio_transcription = None
        if audio_file_path:
            audio_transcription = self.transcribe_audio(audio_file_path)
        elif audio_bytes:
            audio_transcription = self.transcribe_audio_from_bytes(audio_bytes, audio_filename)
        
        # Enhance user message with transcription
        enhanced_message = user_message
        if audio_transcription:
            enhanced_message = f"{user_message}\n\n[Audio transcription: {audio_transcription}]"
        
        return self.chat_completion(
            system_prompt=system_prompt,
            user_message=enhanced_message,
            conversation_history=conversation_history,
            temperature=temperature,
            max_tokens=max_tokens
        )
