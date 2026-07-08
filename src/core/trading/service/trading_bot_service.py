from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from core.trading.service import alpaca_client
from core.trading.service.trading_storage import load_equities, save_equities, equities_path_for_user


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _compute_level_prices(entry_price: float, levels: int, drawdown: float) -> Dict[int, float]:
    return {i + 1: round(entry_price * (1 - drawdown * (i + 1)), 2) for i in range(levels)}


def _get_max_entry_price(symbol: str) -> float:
    try:
        orders = alpaca_client.list_filled_orders(limit=50)
        prices = []
        for order in orders:
            if getattr(order, "symbol", None) != symbol:
                continue
            p = getattr(order, "filled_avg_price", None)
            if p is None:
                continue
            try:
                prices.append(float(p))
            except Exception:
                continue
        return max(prices) if prices else -1.0
    except Exception as e:
        logger.warning(f"[TRADING] Failed to get entry price for {symbol}: {e}")
        return -1.0


def _has_open_order_at_price(symbol: str, price: float) -> bool:
    try:
        orders = alpaca_client.list_open_orders_for_symbol(symbol)
        for o in orders:
            lp = getattr(o, "limit_price", None)
            try:
                if lp is not None and float(lp) == float(price):
                    return True
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[TRADING] Failed to check open orders for {symbol}: {e}")
    return False


def _place_level_order(equities: Dict[str, Any], symbol: str, price: float, level: int) -> None:
    # In the original bot, negative keys represent "already placed" orders.
    levels_map: Dict[str, Any] = equities[symbol].get("levels", {}) or {}
    if str(-level) in levels_map:
        return
    if _has_open_order_at_price(symbol, price):
        levels_map[str(-level)] = price
        levels_map.pop(str(level), None)
        equities[symbol]["levels"] = levels_map
        return
    try:
        alpaca_client.submit_limit_buy(symbol=symbol, qty=1, limit_price=price)
        levels_map[str(-level)] = price
        levels_map.pop(str(level), None)
        equities[symbol]["levels"] = levels_map
        logger.info(f"[TRADING] Placed limit buy {symbol} @ {price} (level {level})")
    except Exception as e:
        logger.warning(f"[TRADING] Error placing order for {symbol} @ {price}: {e}")


def _trade_once(user_id: str) -> None:
    equities = load_equities(user_id)
    if not equities:
        return

    max_position_qty = float(os.environ.get("TRADING_BOT_MAX_POSITION_QTY", "10") or 10)
    cooldown_seconds = float(os.environ.get("TRADING_BOT_COOLDOWN_SECONDS", "30") or 30)

    now = time.time()
    # Per-user in-memory cooldown tracker (symbol -> last action epoch seconds).
    # We keep this volatile by design; it’s a safety throttle, not durable state.
    last_actions = equities.get("__meta__", {}).get("last_actions", {})
    if not isinstance(last_actions, dict):
        last_actions = {}

    for symbol, data in list(equities.items()):
        if symbol == "__meta__":
            continue
        symbol = _normalize_symbol(symbol)
        data = data or {}
        if str(data.get("status", "Off")) != "On":
            continue

        drawdown = float(data.get("drawdown", 0.0) or 0.0)
        existing_levels = data.get("levels", {}) or {}

        # Cooldown: prevent rapid-fire orders for the same symbol.
        last_ts = float(last_actions.get(symbol, 0) or 0)
        if last_ts and (now - last_ts) < cooldown_seconds:
            continue

        # Position cap: do not exceed configured max exposure.
        try:
            current_qty = alpaca_client.get_position_qty(symbol) if alpaca_client.alpaca_is_configured() else 0.0
        except Exception:
            current_qty = 0.0
        if current_qty >= max_position_qty:
            continue

        # Ensure we have an entry price (if no filled orders yet, place initial market buy).
        entry_price = _get_max_entry_price(symbol)
        if entry_price <= 0:
            try:
                # Only place an initial buy when we have no position yet.
                if current_qty <= 0:
                    alpaca_client.submit_market_buy(symbol=symbol, qty=1)
                    last_actions[symbol] = time.time()
                time.sleep(1.5)
                entry_price = _get_max_entry_price(symbol)
            except Exception as e:
                logger.warning(f"[TRADING] Failed to place initial order for {symbol}: {e}")
                continue

        levels_count = int(data.get("levels_count") or 0) or len(
            [k for k in existing_levels.keys() if str(k).lstrip("-").isdigit()]
        ) or 1
        level_prices = _compute_level_prices(entry_price, levels_count, drawdown)

        # Ensure forward levels exist for any missing keys.
        for level, price in level_prices.items():
            if str(level) not in existing_levels and str(-level) not in existing_levels:
                existing_levels[str(level)] = price

        data["entry_price"] = float(entry_price)
        data["levels"] = existing_levels
        data["position"] = int(data.get("position") or 0) or 1

        # Place limit orders for all non-placed forward levels.
        for level, price in level_prices.items():
            if str(level) in data["levels"]:
                _place_level_order(equities, symbol, price, level)
                last_actions[symbol] = time.time()

        equities[symbol] = data

    equities["__meta__"] = {"last_actions": last_actions}
    save_equities(user_id, equities)


@dataclass
class _RunnerState:
    running: bool = False
    interval_seconds: int = 5
    thread: Optional[threading.Thread] = None
    stop_event: threading.Event = threading.Event()


class TradingBotService:
    """
    Minimal per-user trading loop runner.
    - Explicit start/stop via API (no auto-start on boot).
    - Stores config/state in JSON per user.
    """

    _states: Dict[str, _RunnerState] = {}
    _lock = threading.Lock()

    @classmethod
    def storage_path(cls, user_id: str) -> str:
        return str(equities_path_for_user(user_id))

    @classmethod
    def get_status(cls, user_id: str) -> Tuple[bool, int]:
        with cls._lock:
            st = cls._states.get(user_id)
            if not st:
                return False, int(os.environ.get("TRADING_BOT_INTERVAL_SECONDS", "5") or 5)
            return bool(st.running), int(st.interval_seconds)

    @classmethod
    def start(cls, user_id: str, interval_seconds: Optional[int] = None) -> None:
        if os.environ.get("TRADING_BOT_ENABLED", "true").strip().lower() not in ("1", "true", "yes"):
            raise RuntimeError("Trading bot is disabled on this server (TRADING_BOT_ENABLED=false).")
        if not alpaca_client.alpaca_is_configured():
            raise RuntimeError("Alpaca is not configured (set ALPACA_API_KEY and ALPACA_API_SECRET).")

        with cls._lock:
            st = cls._states.get(user_id) or _RunnerState()
            if st.running and st.thread and st.thread.is_alive():
                cls._states[user_id] = st
                return

            st.stop_event = threading.Event()
            st.interval_seconds = int(
                interval_seconds
                or os.environ.get("TRADING_BOT_INTERVAL_SECONDS", "5").strip()
                or 5
            )
            st.running = True

            def _loop():
                logger.info(f"[TRADING] Runner started: user={user_id} interval={st.interval_seconds}s")
                try:
                    while not st.stop_event.is_set():
                        try:
                            _trade_once(user_id)
                        except Exception as e:
                            logger.warning(f"[TRADING] Trade loop error (user={user_id}): {e}")
                        st.stop_event.wait(timeout=float(st.interval_seconds))
                finally:
                    logger.info(f"[TRADING] Runner stopped: user={user_id}")

            st.thread = threading.Thread(target=_loop, daemon=True)
            st.thread.start()
            cls._states[user_id] = st

    @classmethod
    def stop(cls, user_id: str) -> None:
        with cls._lock:
            st = cls._states.get(user_id)
            if not st:
                return
            st.running = False
            try:
                st.stop_event.set()
            except Exception:
                pass

