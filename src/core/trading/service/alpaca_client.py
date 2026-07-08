from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional


def alpaca_is_configured() -> bool:
    return bool(os.environ.get("ALPACA_API_KEY", "").strip() and os.environ.get("ALPACA_API_SECRET", "").strip())


@lru_cache(maxsize=1)
def _get_alpaca_rest():
    import alpaca_trade_api as tradeapi  # lazy import

    key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET", "").strip()
    base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").strip().rstrip("/") + "/"
    return tradeapi.REST(key, secret, base_url, api_version="v2")


def fetch_portfolio() -> List[Dict[str, Any]]:
    api = _get_alpaca_rest()
    positions = api.list_positions()
    portfolio: List[Dict[str, Any]] = []
    for pos in positions:
        portfolio.append(
            {
                "symbol": getattr(pos, "symbol", ""),
                "qty": getattr(pos, "qty", None),
                "entry_price": getattr(pos, "avg_entry_price", None),
                "current_price": getattr(pos, "current_price", None),
                "unrealized_pl": getattr(pos, "unrealized_pl", None),
                "side": "buy",
            }
        )
    return portfolio


def fetch_open_orders() -> List[Dict[str, Any]]:
    api = _get_alpaca_rest()
    orders = api.list_orders(status="open")
    open_orders: List[Dict[str, Any]] = []
    for order in orders:
        open_orders.append(
            {
                "symbol": getattr(order, "symbol", ""),
                "qty": getattr(order, "qty", None),
                "limit_price": getattr(order, "limit_price", None),
                "side": getattr(order, "side", "buy"),
            }
        )
    return open_orders


def get_latest_trade_price(symbol: str) -> float:
    api = _get_alpaca_rest()
    trade = api.get_latest_trade(symbol)
    price = getattr(trade, "price", None)
    try:
        return float(price)
    except Exception:
        return -1.0


def get_bars(
    symbol: str,
    timeframe: str = "1Day",
    limit: int = 200,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch OHLCV bars for a symbol from Alpaca.

    Returns a list of dicts: {t, o, h, l, c, v}.
    `t` is an ISO timestamp string.
    """
    api = _get_alpaca_rest()

    # Alpaca's python SDK returns a DataFrame-like object for get_bars; normalize to dicts.
    bars = api.get_bars(symbol, timeframe, start=start, end=end, limit=limit)

    rows: List[Dict[str, Any]] = []
    try:
        df = bars.df  # type: ignore[attr-defined]
        if df is None:
            return rows

        if "symbol" in df.columns:
            df = df[df["symbol"] == symbol]

        # Normalize index/column timestamp
        if "timestamp" in df.columns:
            ts = df["timestamp"]
        else:
            ts = df.index

        for idx, row in df.iterrows():
            t_val = ts.loc[idx] if hasattr(ts, "loc") else ts[idx]  # type: ignore[index]
            t = getattr(t_val, "isoformat", lambda: str(t_val))()
            rows.append(
                {
                    "t": t,
                    "o": float(row.get("open")),
                    "h": float(row.get("high")),
                    "l": float(row.get("low")),
                    "c": float(row.get("close")),
                    "v": float(row.get("volume")),
                }
            )
        return rows
    except Exception:
        # Fallback: attempt best-effort iteration
        try:
            for b in bars:  # type: ignore[assignment]
                t_val = getattr(b, "t", None) or getattr(b, "timestamp", None)
                t = getattr(t_val, "isoformat", lambda: str(t_val))() if t_val is not None else ""
                rows.append(
                    {
                        "t": t,
                        "o": float(getattr(b, "o", 0.0) or 0.0),
                        "h": float(getattr(b, "h", 0.0) or 0.0),
                        "l": float(getattr(b, "l", 0.0) or 0.0),
                        "c": float(getattr(b, "c", 0.0) or 0.0),
                        "v": float(getattr(b, "v", 0.0) or 0.0),
                    }
                )
        except Exception:
            return []
        return rows


def list_open_orders_for_symbol(symbol: str):
    api = _get_alpaca_rest()
    return api.list_orders(status="open", symbols=symbol)


def list_filled_orders(limit: int = 50):
    api = _get_alpaca_rest()
    return api.list_orders(status="filled", limit=limit)


def get_position_qty(symbol: str) -> float:
    api = _get_alpaca_rest()
    try:
        pos = api.get_position(symbol)
    except Exception:
        return 0.0
    qty = getattr(pos, "qty", 0)
    try:
        return float(qty)
    except Exception:
        return 0.0


def submit_market_buy(symbol: str, qty: int = 1):
    api = _get_alpaca_rest()
    return api.submit_order(symbol=symbol, qty=qty, side="buy", type="market", time_in_force="gtc")


def submit_limit_buy(symbol: str, qty: int, limit_price: float):
    api = _get_alpaca_rest()
    return api.submit_order(
        symbol=symbol,
        qty=qty,
        side="buy",
        type="limit",
        time_in_force="gtc",
        limit_price=limit_price,
    )

