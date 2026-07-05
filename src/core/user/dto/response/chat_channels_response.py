from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TelegramChannelStatus(BaseModel):
    connected: bool
    chat_id: Optional[str] = None


class ChatChannelsResponse(BaseModel):
    telegram: TelegramChannelStatus


class ChatChannelDisconnectResponse(BaseModel):
    message: str
