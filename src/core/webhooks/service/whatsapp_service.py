import json
import os
from pathlib import Path

import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Service for sending messages via Meta's WhatsApp Cloud API"""

    def __init__(self):
        self.api_key = os.getenv("META_API_KEY")
        self.base_url = "https://graph.facebook.com/v24.0"

    def create_registration_flow(self, phone_id: str) -> Optional[str]:
        """
        Create the registration Flow template in Meta using the local JSON definition.

        Args:
            phone_id: Meta phone number ID to associate with the Flow

        Returns:
            Optional[str]: The created flow_id, if successful
        """
        json_path = (
            Path(__file__).resolve().parent.parent
            / "templates"
            / "registration_form_flow.json"
        )

        try:
            with open(json_path, "r", encoding="utf-8") as flow_file:
                flow_definition = json.load(flow_file)
        except FileNotFoundError:
            logger.error(f"Flow definition file not found: {json_path}")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        url = f"{self.base_url}/{phone_id}/flows"
        payload = {
            "flow": flow_definition
        }

        try:
            logger.info(f"Creating WhatsApp registration Flow for {phone_id}")
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            flow_id = data.get("id")
            logger.info(f"Registration Flow created with id: {flow_id}")
            return flow_id

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to create WhatsApp registration Flow: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response content: {e.response.text}")
            return None

    def send_message(
        self,
        phone_id: str,
        recipient_phone: str,
        message_text: str,
        preview_url: bool = False
    ) -> bool:
        """
        Send a text message via WhatsApp Cloud API

        Args:
            phone_id: The phone number ID from Meta webhook metadata
            recipient_phone: The recipient's WhatsApp ID (phone number)
            message_text: The message to send
            preview_url: Whether to show URL preview in the message

        Returns:
            bool: True if message sent successfully, False otherwise
        """
        url = f"{self.base_url}/{phone_id}/messages"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "text",
            "text": {
                "preview_url": preview_url,
                "body": message_text
            }
        }

        try:
            logger.info(f"Sending WhatsApp text message to {recipient_phone}")
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()

            logger.info(
                f"WhatsApp message sent successfully: {response.json()}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send WhatsApp message: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response content: {e.response.text}")
            return False

    def send_registration_template(
        self,
        phone_id: str,
        recipient_phone: str
    ) -> bool:
        """
        Send registration template via WhatsApp Cloud API
        Args:
            phone_id: The phone number ID from Meta webhook metadata
            recipient_phone: The recipient's WhatsApp ID (phone number)
        Returns:
            bool: True if template sent successfully, False otherwise
        """
        url = f"{self.base_url}/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "template",
            "template": {
                "name": "registration",
                "language": {"code": "en"},
                "components": [
                    {
                        "type": "button",
                        "sub_type": "flow",
                        "index": "0",
                        "parameters": [
                            {
                                "type": "action",
                                "action": {
                                    "flow_token": "2002104030434872"
                                }
                            }
                        ]
                    }
                ]
            }
        }
        try:
            # Log API key info for debugging
            logger.info(f"API Key (first 30 chars): {self.api_key[:30]}...")
            logger.info(f"API Key length: {len(self.api_key)}")
            logger.info(f"Base URL: {self.base_url}")
            logger.info(f"Full URL: {url}")
            logger.info(
                f"Sending WhatsApp registration template to {recipient_phone}")

            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            logger.info(
                f"WhatsApp template sent successfully: {response.json()}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send WhatsApp template: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response content: {e.response.text}")
            return False

    def send_message_receipt(
        self,
        phone_id: str,
        recipient_phone: str,
        image_url: str,
        caption: Optional[str] = None
    ) -> bool:
        """
        Send a receipt image via WhatsApp Cloud API

        Args:
            phone_id: The phone number ID from Meta webhook metadata
            recipient_phone: The recipient's WhatsApp ID (phone number)
            image_url: The URL of the receipt image to send (must be publicly accessible)
            caption: Optional caption for the receipt

        Returns:
            bool: True if receipt sent successfully, False otherwise
        """
        url = f"{self.base_url}/{phone_id}/messages"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "image",
            "image": {
                "link": image_url
            }
        }

        # Add caption if provided
        if caption:
            payload["image"]["caption"] = caption

        try:
            logger.info(f"Sending WhatsApp receipt to {recipient_phone}")
            logger.debug(f"Receipt URL: {image_url}")
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()

            logger.info(
                f"WhatsApp receipt sent successfully: {response.json()}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send WhatsApp receipt: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response content: {e.response.text}")
            return False

    def upload_audio_media(self, phone_id: str, audio_bytes: bytes, mime_type: str = "audio/mpeg") -> Optional[str]:
        """Upload audio bytes to WhatsApp Cloud API and return the media ID."""
        url = f"{self.base_url}/{phone_id}/media"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        filename = "briefing.mp3" if mime_type == "audio/mpeg" else "briefing.audio"

        try:
            files = {"file": (filename, audio_bytes, mime_type)}
            data = {"messaging_product": "whatsapp", "type": mime_type}
            logger.info("Uploading WhatsApp audio media (%s bytes)", len(audio_bytes))
            response = requests.post(url, headers=headers, data=data, files=files, timeout=60)
            response.raise_for_status()
            media_id = response.json().get("id")
            if not media_id:
                logger.error("WhatsApp media upload returned no id: %s", response.text)
                return None
            return media_id
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to upload WhatsApp audio media: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response content: {e.response.text}")
            return None

    def send_audio(
        self,
        phone_id: str,
        recipient_phone: str,
        audio_bytes: bytes,
        mime_type: str = "audio/mpeg",
    ) -> bool:
        """Send an audio message via WhatsApp Cloud API."""
        media_id = self.upload_audio_media(phone_id, audio_bytes, mime_type=mime_type)
        if not media_id:
            return False

        url = f"{self.base_url}/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "audio",
            "audio": {"id": media_id},
        }

        try:
            logger.info(f"Sending WhatsApp audio message to {recipient_phone}")
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            logger.info("WhatsApp audio sent successfully: %s", response.json())
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send WhatsApp audio: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response content: {e.response.text}")
            return False
