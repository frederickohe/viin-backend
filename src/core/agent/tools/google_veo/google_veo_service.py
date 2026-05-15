from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from core.cloudstorage.service.storageservice import StorageService

_env_path = Path(__file__).resolve().parents[5] / ".env"
load_dotenv(dotenv_path=_env_path)


class GoogleVeoGenerationError(RuntimeError):
    pass


def _extract_first_url(value: Any) -> str | None:
    if isinstance(value, str):
        m = re.search(r"https?://\S+", value)
        return m.group(0) if m else None
    if isinstance(value, dict):
        for key in ("fileUri", "file_uri", "uri", "downloadUri", "download_uri"):
            uri = value.get(key)
            if isinstance(uri, str) and uri.strip().startswith(("http://", "https://", "gs://")):
                return uri.strip()
        for v in value.values():
            url = _extract_first_url(v)
            if url:
                return url
    if isinstance(value, list):
        for v in value:
            url = _extract_first_url(v)
            if url:
                return url
    return None


class GoogleVeoService:
    """
    Veo generation endpoints vary (Vertex AI / region / publisher model).
    This project is configured via env vars (as per your .env):
    - GOOGLE_API_KEY
    - VEO_GENERATE_URL (base URL, typically https://generativelanguage.googleapis.com/v1beta)
    - VEO_MODEL (e.g. veo-3.1-generate-preview)

    Auth:
    - query param `?key=GOOGLE_API_KEY` (default)
    - OR header `x-goog-api-key` if VEO_USE_X_GOOG_API_KEY=true
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("GOOGLE_API_KEY", "")
        self._base_url = os.environ.get("VEO_GENERATE_URL", "").rstrip("/")
        self._model = os.environ.get("VEO_MODEL", "").strip()
        self._use_x_goog = os.environ.get("VEO_USE_X_GOOG_API_KEY", "false").lower() == "true"

        if not self._api_key:
            raise GoogleVeoGenerationError("GOOGLE_API_KEY is not set")
        if not self._base_url:
            raise GoogleVeoGenerationError("VEO_GENERATE_URL is not set")
        if not self._model:
            raise GoogleVeoGenerationError("VEO_MODEL is not set")

        self._generate_url = f"{self._base_url}/models/{self._model}:generateContent"

    async def generate_video_url(self, prompt: str, *, user_id: str | None = None) -> str:
        # Keep this aligned with the Generative Language `generateContent` schema.
        # Arbitrary fields like `user_id` are rejected with INVALID_ARGUMENT.
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ]
        }

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        params = {}
        if self._use_x_goog:
            headers["x-goog-api-key"] = self._api_key
        else:
            params["key"] = self._api_key

        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(self._generate_url, headers=headers, params=params, json=payload)

        if resp.status_code >= 400:
            raise GoogleVeoGenerationError(f"Google Veo API error {resp.status_code}: {resp.text}")

        try:
            data = resp.json()
        except Exception as e:
            raise GoogleVeoGenerationError(f"Invalid JSON response from Google Veo API: {e}") from e

        extracted = _extract_first_url(data)
        if not extracted:
            raise GoogleVeoGenerationError("No URL found in Google Veo API response")
        return extracted

    async def generate_video_and_store(self, prompt: str, *, user_id: str | None = None) -> str:
        """
        Generates a video with Veo, downloads it, uploads to Contabo storage,
        and returns the Contabo URL (suitable for streaming by the frontend).
        """
        source_url = await self.generate_video_url(prompt, user_id=user_id)

        # Stream download to a temp file to avoid holding large videos in memory.
        suffix = ".mp4"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = tmp.name

            async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
                async with client.stream("GET", source_url) as r:
                    if r.status_code >= 400:
                        raise GoogleVeoGenerationError(f"Failed to download generated video ({r.status_code})")
                    with open(tmp_path, "wb") as f:
                        async for chunk in r.aiter_bytes():
                            if chunk:
                                f.write(chunk)

            storage = StorageService()
            object_name = f"{uuid.uuid4().hex}{suffix}"
            with open(tmp_path, "rb") as f:
                # Increase timeout for large files.
                contabo_url = storage.upload_file(
                    f,
                    object_name,
                    content_type="video/mp4",
                    timeout_seconds=300,
                    folder="generated-videos",
                )

            return contabo_url
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

