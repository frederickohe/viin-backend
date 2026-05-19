from __future__ import annotations

import asyncio
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


class GoogleVeoTimeoutError(GoogleVeoGenerationError):
    """Raised when Veo video generation does not complete in time."""

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


def _extract_video_uri_from_operation(data: dict[str, Any]) -> str | None:
    """Parse completed Veo long-running operation JSON for a video download URI."""
    response = data.get("response")
    if not isinstance(response, dict):
        return None

    generate_video = response.get("generateVideoResponse") or response.get("generate_video_response")
    if isinstance(generate_video, dict):
        samples = generate_video.get("generatedSamples") or generate_video.get("generated_samples")
        if isinstance(samples, list) and samples:
            first = samples[0]
            if isinstance(first, dict):
                video = first.get("video")
                if isinstance(video, dict):
                    uri = video.get("uri")
                    if isinstance(uri, str) and uri.strip():
                        return uri.strip()

    return _extract_first_url(data)


class GoogleVeoService:
    """
    Veo video generation via Google's Generative Language REST API.

    Veo is asynchronous: POST :predictLongRunning, then poll the operation until done.

    Env vars:
    - GOOGLE_API_KEY
    - VEO_GENERATE_URL (base URL, typically https://generativelanguage.googleapis.com/v1beta)
    - VEO_MODEL (e.g. veo-3.1-generate-preview)
    - VEO_USE_X_GOOG_API_KEY (default false)
    - VEO_POLL_INTERVAL_SECONDS (default 10)
    - VEO_MAX_POLL_SECONDS (default 600)
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("GOOGLE_API_KEY", "")
        self._base_url = os.environ.get("VEO_GENERATE_URL", "").rstrip("/")
        self._model = os.environ.get("VEO_MODEL", "").strip()
        self._use_x_goog = os.environ.get("VEO_USE_X_GOOG_API_KEY", "false").lower() == "true"
        self._poll_interval = float(os.environ.get("VEO_POLL_INTERVAL_SECONDS", "10"))
        self._max_poll_seconds = float(os.environ.get("VEO_MAX_POLL_SECONDS", "600"))

        if not self._api_key:
            raise GoogleVeoGenerationError("GOOGLE_API_KEY is not set")
        if not self._base_url:
            raise GoogleVeoGenerationError("VEO_GENERATE_URL is not set")
        if not self._model:
            raise GoogleVeoGenerationError("VEO_MODEL is not set")

        self._start_url = f"{self._base_url}/models/{self._model}:predictLongRunning"

    def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        params: dict[str, str] = {}
        if self._use_x_goog:
            headers["x-goog-api-key"] = self._api_key
        else:
            params["key"] = self._api_key
        return headers, params

    def _http_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=30.0,
            read=max(self._max_poll_seconds, 120.0),
            write=120.0,
            pool=30.0,
        )

    async def generate_video_url(self, prompt: str, *, user_id: str | None = None) -> str:
        # Veo does not accept arbitrary user_id on the request body.
        headers, params = self._auth()
        payload: dict[str, Any] = {
            "instances": [{"prompt": prompt}],
        }

        timeout = self._http_timeout()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                start_resp = await client.post(
                    self._start_url,
                    headers=headers,
                    params=params,
                    json=payload,
                )
        except httpx.TimeoutException as e:
            raise GoogleVeoTimeoutError(
                "Google Veo API did not respond when starting video generation."
            ) from e

        if start_resp.status_code >= 400:
            raise GoogleVeoGenerationError(
                f"Google Veo API error {start_resp.status_code}: {start_resp.text}"
            )

        try:
            start_data = start_resp.json()
        except Exception as e:
            raise GoogleVeoGenerationError(f"Invalid JSON from Google Veo API: {e}") from e

        operation_name = start_data.get("name")
        if not isinstance(operation_name, str) or not operation_name.strip():
            raise GoogleVeoGenerationError(
                "Google Veo API did not return an operation name for polling."
            )

        operation_name = operation_name.strip().lstrip("/")
        poll_url = f"{self._base_url}/{operation_name}"

        elapsed = 0.0
        while elapsed < self._max_poll_seconds:
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    poll_resp = await client.get(poll_url, headers=headers, params=params)
            except httpx.TimeoutException as e:
                raise GoogleVeoTimeoutError(
                    "Google Veo API timed out while polling video generation status."
                ) from e

            if poll_resp.status_code >= 400:
                raise GoogleVeoGenerationError(
                    f"Google Veo poll error {poll_resp.status_code}: {poll_resp.text}"
                )

            try:
                poll_data = poll_resp.json()
            except Exception as e:
                raise GoogleVeoGenerationError(f"Invalid JSON from Google Veo poll: {e}") from e

            if poll_data.get("error"):
                raise GoogleVeoGenerationError(f"Google Veo generation failed: {poll_data['error']}")

            if poll_data.get("done"):
                video_uri = _extract_video_uri_from_operation(poll_data)
                if not video_uri:
                    raise GoogleVeoGenerationError(
                        "No video URI found in completed Google Veo operation. "
                        "Confirm VEO_MODEL is a Veo model (e.g. veo-3.1-generate-preview)."
                    )
                return video_uri

        raise GoogleVeoTimeoutError(
            f"Google Veo video generation did not complete within {int(self._max_poll_seconds)} seconds."
        )

    async def generate_video_and_store(self, prompt: str, *, user_id: str | None = None) -> str:
        """
        Generates a video with Veo, downloads it, uploads to Contabo storage,
        and returns the Contabo URL (suitable for streaming by the frontend).
        """
        source_url = await self.generate_video_url(prompt, user_id=user_id)

        suffix = ".mp4"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = tmp.name

            dl_headers, dl_params = self._auth()
            download_timeout = self._http_timeout()
            async with httpx.AsyncClient(
                timeout=download_timeout, follow_redirects=True
            ) as client:
                async with client.stream(
                    "GET", source_url, headers=dl_headers, params=dl_params
                ) as r:
                    if r.status_code >= 400:
                        raise GoogleVeoGenerationError(
                            f"Failed to download generated video ({r.status_code})"
                        )
                    with open(tmp_path, "wb") as f:
                        async for chunk in r.aiter_bytes():
                            if chunk:
                                f.write(chunk)

            storage = StorageService()
            object_name = f"{uuid.uuid4().hex}{suffix}"
            with open(tmp_path, "rb") as f:
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
