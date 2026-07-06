"""شمارنده‌های عملکردِ روزانهٔ ربات فروش (در حافظه + ذخیرهٔ سبک روی دیسک تا با ری‌استارت صفر نشود).

هر رویداد کلی و به‌تفکیکِ کانال شمرده می‌شود: reply/image/order/receipt/handoff/wrist_media.
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_HERE, "data", "metrics.json")
_M: dict = {"day": "", "events": {}, "totals": {}}


def _today():
    try:
        import clock
        return clock.tehran_now().strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return time.strftime("%Y-%m-%d")


def _load():
    global _M
    try:
        with open(_FILE, encoding="utf-8") as f:
            _M = json.load(f)
    except Exception:  # noqa: BLE001
        _M = {"day": _today(), "events": {}, "totals": {}}
    _M.setdefault("events", {})
    _M.setdefault("totals", {})


def _save():
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_M, f, ensure_ascii=False)
        os.replace(tmp, _FILE)
    except Exception:  # noqa: BLE001
        pass


def _roll():
    d = _today()
    if _M.get("day") != d:
        _M["day"] = d
        _M["events"] = {}


def bump(event, channel="", n=1):
    """یک رویداد را +n کن (کلی و به‌تفکیکِ کانال) — امروز و مجموعِ کل."""
    try:
        _roll()
        ev = _M.setdefault("events", {})
        tot = _M.setdefault("totals", {})
        for k in (event, (event + ":" + channel) if channel else None):
            if k:
                ev[k] = ev.get(k, 0) + n
                tot[k] = tot.get(k, 0) + n
        _save()
    except Exception:  # noqa: BLE001
        pass


def snapshot():
    _roll()
    return {"day": _M.get("day"), "today": dict(_M.get("events", {})), "totals": dict(_M.get("totals", {}))}


_load()
_roll()
