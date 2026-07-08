from __future__ import annotations

import jwt
from another_fastapi_jwt_auth import AuthJWT
from another_fastapi_jwt_auth.exceptions import MissingTokenError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.auth.controller.authcontroller import get_db
from core.llmclient.llmclient import LLMClient
from core.trading.dto.trading_dtos import (
    TradingBasicResponse,
    TradingChatRequest,
    TradingChatResponse,
    TradingEquitiesResponse,
    TradingEquity,
    TradingEquityCreateRequest,
    TradingEquityToggleResponse,
    TradingStatusResponse,
)
from core.trading.service import alpaca_client
from core.trading.service.trading_bot_service import TradingBotService
from core.trading.service.trading_storage import load_equities, save_equities
from core.user.service.user_service import UserService

trading_routes = APIRouter()


def validate_token(authjwt: AuthJWT = Depends()):
    try:
        authjwt.jwt_required()
        return authjwt
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired. Please log in again.")
    except MissingTokenError:
        raise HTTPException(status_code=401, detail="No token found. Please log in.")
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(exc)}")


def _current_user(authjwt: AuthJWT, db: Session):
    email = authjwt.get_jwt_subject()
    return UserService(db).get_current_user(email)


def _to_equity(symbol: str, raw) -> TradingEquity:
    raw = raw or {}
    return TradingEquity(
        symbol=symbol,
        position=int(raw.get("position") or 0),
        entry_price=float(raw.get("entry_price") or 0.0),
        drawdown=float(raw.get("drawdown") or 0.0),
        status=str(raw.get("status") or "Off"),
        levels={str(k): float(v) for k, v in (raw.get("levels") or {}).items()},
    )


@trading_routes.get("/status", response_model=TradingStatusResponse)
def trading_status(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    user = _current_user(authjwt, db)
    running, interval = TradingBotService.get_status(str(user.id))
    return TradingStatusResponse(
        running=running,
        interval_seconds=interval,
        alpaca_configured=alpaca_client.alpaca_is_configured(),
        storage_path=TradingBotService.storage_path(str(user.id)),
    )


@trading_routes.get("/equities", response_model=TradingEquitiesResponse)
def list_equities(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    user = _current_user(authjwt, db)
    raw = load_equities(str(user.id))
    equities = [_to_equity(sym, data) for sym, data in (raw or {}).items()]
    equities.sort(key=lambda e: e.symbol)
    return TradingEquitiesResponse(equities=equities)


@trading_routes.post("/equities", response_model=TradingEquity)
def add_equity(
    payload: TradingEquityCreateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    user = _current_user(authjwt, db)
    symbol = payload.symbol.strip().upper()
    drawdown = float(payload.drawdown_percent) / 100.0

    raw = load_equities(str(user.id))
    entry_price = alpaca_client.get_latest_trade_price(symbol) if alpaca_client.alpaca_is_configured() else -1.0
    if entry_price <= 0:
        entry_price = 0.0

    level_prices = {str(i + 1): round(entry_price * (1 - drawdown * (i + 1)), 2) for i in range(payload.levels)}
    raw[symbol] = {
        "position": 0,
        "entry_price": float(entry_price),
        "levels": level_prices,
        "drawdown": float(drawdown),
        "status": "Off",
    }
    save_equities(str(user.id), raw)
    return _to_equity(symbol, raw[symbol])


@trading_routes.delete("/equities/{symbol}", response_model=TradingBasicResponse)
def remove_equity(
    symbol: str,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    user = _current_user(authjwt, db)
    sym = symbol.strip().upper()
    raw = load_equities(str(user.id))
    if sym not in raw:
        raise HTTPException(status_code=404, detail="Equity not found")
    del raw[sym]
    save_equities(str(user.id), raw)
    return TradingBasicResponse(message=f"Removed {sym}")


@trading_routes.post("/equities/{symbol}/toggle", response_model=TradingEquityToggleResponse)
def toggle_equity(
    symbol: str,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    user = _current_user(authjwt, db)
    sym = symbol.strip().upper()
    raw = load_equities(str(user.id))
    if sym not in raw:
        raise HTTPException(status_code=404, detail="Equity not found")
    current = str((raw[sym] or {}).get("status") or "Off")
    raw[sym]["status"] = "On" if current == "Off" else "Off"
    save_equities(str(user.id), raw)
    return TradingEquityToggleResponse(symbol=sym, status=raw[sym]["status"])


@trading_routes.post("/start", response_model=TradingBasicResponse)
def start_bot(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    user = _current_user(authjwt, db)
    try:
        TradingBotService.start(str(user.id))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return TradingBasicResponse(message="Trading bot started")


@trading_routes.post("/stop", response_model=TradingBasicResponse)
def stop_bot(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    user = _current_user(authjwt, db)
    TradingBotService.stop(str(user.id))
    return TradingBasicResponse(message="Trading bot stopped")


@trading_routes.post("/chat", response_model=TradingChatResponse)
def trading_chat(
    payload: TradingChatRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    _ = _current_user(authjwt, db)
    if not alpaca_client.alpaca_is_configured():
        raise HTTPException(status_code=503, detail="Alpaca is not configured (set ALPACA_API_KEY/SECRET).")

    portfolio_data = alpaca_client.fetch_portfolio()
    open_orders = alpaca_client.fetch_open_orders()

    system_prompt = (
        "You are an AI portfolio manager responsible for analyzing the user's portfolio.\n"
        "Tasks:\n"
        "1) Evaluate risk exposures of current holdings\n"
        "2) Analyze open limit orders and their potential impact\n"
        "3) Provide insights on portfolio health, diversification, and trade adjustments\n"
        "4) Speculate on market outlook given the context provided\n"
        "5) Identify potential market risks and suggest risk management strategies\n\n"
        f"Portfolio: {portfolio_data}\n\n"
        f"Open orders: {open_orders}\n"
    )

    response = LLMClient().chat_completion(system_prompt=system_prompt, user_message=payload.message, max_tokens=600)
    return TradingChatResponse(response=response or "", success=True)

