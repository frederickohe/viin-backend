from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TradingEquity(BaseModel):
    symbol: str
    position: int = 0
    entry_price: float = 0.0
    drawdown: float = Field(..., ge=0.0, le=1.0, description="Drawdown as a fraction (e.g. 0.05 for 5%).")
    status: Literal["On", "Off"] = "Off"
    levels: Dict[str, float] = Field(default_factory=dict, description="Level map. Keys may be numeric strings.")


class TradingStatusResponse(BaseModel):
    running: bool
    interval_seconds: int
    alpaca_configured: bool
    storage_path: str


class TradingEquitiesResponse(BaseModel):
    equities: List[TradingEquity]


class TradingEquityCreateRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    levels: int = Field(..., ge=1, le=500)
    drawdown_percent: float = Field(..., gt=0.0, le=99.0)


class TradingEquityToggleResponse(BaseModel):
    symbol: str
    status: Literal["On", "Off"]


class TradingChatRequest(BaseModel):
    message: str = Field(..., min_length=1)


class TradingChatResponse(BaseModel):
    response: str
    success: bool = True


class TradingBasicResponse(BaseModel):
    message: str


class MarketBar(BaseModel):
    t: str = Field(..., description="ISO timestamp for the bar.")
    o: float
    h: float
    l: float
    c: float
    v: float


class MarketBarsResponse(BaseModel):
    symbol: str
    timeframe: str
    bars: List[MarketBar]

