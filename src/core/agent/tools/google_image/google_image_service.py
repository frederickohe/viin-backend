from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

import os

_env_path = Path(__file__).resolve().parents[5] / ".env"
load_dotenv(dotenv_path=_env_path)


class GoogleImageGenerationError(RuntimeError):
    pass


class GoogleImageTimeoutError(GoogleImageGenerationError):
    """Raised when the Google image API does not respond in time."""

    pass


def _extract_first_mime_type(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("inlineData", "inline_data"):
            inline = value.get(key)
            if isinstance(inline, dict):
                mime = inline.get("mimeType") or inline.get("mime_type")
                if isinstance(mime, str) and mime.strip():
                    return mime.strip()
        for v in value.values():
            found = _extract_first_mime_type(v)
            if found:
                return found
    if isinstance(value, list):
        for v in value:
            found = _extract_first_mime_type(v)
            if found:
                return found
    return None


def _extract_first_base64_image(value: Any) -> str | None:
    """
    Gemini image generation commonly returns base64 bytes under:
    - candidates[].content.parts[].inlineData.data
    - candidates[].content.parts[].inline_data.data
    """
    if isinstance(value, dict):
        if "inlineData" in value and isinstance(value["inlineData"], dict):
            data = value["inlineData"].get("data")
            if isinstance(data, str) and data.strip():
                return data
        if "inline_data" in value and isinstance(value["inline_data"], dict):
            data = value["inline_data"].get("data")
            if isinstance(data, str) and data.strip():
                return data
        for v in value.values():
            b64 = _extract_first_base64_image(v)
            if b64:
                return b64
    if isinstance(value, list):
        for v in value:
            b64 = _extract_first_base64_image(v)
            if b64:
                return b64
    return None


class GoogleImageService:
    """
    Nana Banana image generation via Google's Generative Language REST API.

    Env vars (as per your .env):
    - GOOGLE_API_KEY
    - NANA_BANANA_BASE_URL (default: https://generativelanguage.googleapis.com/v1beta)
    - NANA_BANANA_MODEL (e.g. gemini-3.1-flash-image-preview)
    - NANA_BANANA_HTTP_READ_TIMEOUT (seconds, default 600; image generation often exceeds 120s)

    Auth:
    - query param `?key=GOOGLE_API_KEY` (default)
    - OR header `x-goog-api-key` if NANA_BANANA_USE_X_GOOG_API_KEY=true
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("GOOGLE_API_KEY", "")
        self._base_url = os.environ.get("NANA_BANANA_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        self._model = os.environ.get("NANA_BANANA_MODEL", "").strip()
        self._use_x_goog = os.environ.get("NANA_BANANA_USE_X_GOOG_API_KEY", "false").lower() == "true"
        self._read_timeout = float(os.environ.get("NANA_BANANA_HTTP_READ_TIMEOUT", "600"))
        self.last_mime_type: str | None = None

        if not self._api_key:
            raise GoogleImageGenerationError("GOOGLE_API_KEY is not set")
        if not self._base_url:
            raise GoogleImageGenerationError("NANA_BANANA_BASE_URL is not set")
        if not self._model:
            raise GoogleImageGenerationError("NANA_BANANA_MODEL is not set")

    async def generate_image_base64(self, prompt: str, *, user_id: str | None = None) -> str:
        url = f"{self._base_url}/models/{self._model}:generateContent"

        # Minimal generateContent payload. If your frontend/tooling needs richer
        # generation configs, extend this payload.
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ]
        }
        # Note: Google's Generative Language `generateContent` does not accept an
        # arbitrary `user_id` field; passing it causes INVALID_ARGUMENT.
        # Keep `user_id` only for our own logging/telemetry if needed.

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        params = {}
        if self._use_x_goog:
            headers["x-goog-api-key"] = self._api_key
        else:
            params["key"] = self._api_key

        timeout = httpx.Timeout(
            connect=30.0,
            read=self._read_timeout,
            write=120.0,
            pool=30.0,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, params=params, json=payload)
        except httpx.TimeoutException as e:
            raise GoogleImageTimeoutError(
                "Google image API request timed out; try again or shorten the prompt."
            ) from e

        if resp.status_code >= 400:
            raise GoogleImageGenerationError(f"Google image API error {resp.status_code}: {resp.text}")

        try:
            data = resp.json()
        except Exception as e:
            raise GoogleImageGenerationError(f"Invalid JSON response from Google image API: {e}") from e

        extracted = _extract_first_base64_image(data)
        if not extracted:
            raise GoogleImageGenerationError("No base64 image data found in Google image API response")
        self.last_mime_type = _extract_first_mime_type(data) or "image/png"
        return extracted

