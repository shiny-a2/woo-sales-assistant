"""بافرِ گفتگوهای رباتِ فروشِ تلگرام (@JavaherianAIbot) برای نمایش/پایش در داشبورد.

هر تبادل (پیامِ مشتری + پاسخِ ربات) اینجا ثبت می‌شود؛ نیز شمارشِ کاربرانِ فعال و آخرین فعالیت.
روی دیسک (data/tg_chats.json) ماندگار است تا با ری‌استارتِ مغز «گفتگوهای اخیر» خالی نشود.
"""
from __future__ import annotations

import json
import os
from collections import deque

_HERE = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_HERE, "data", "tg_chats.json")

_CHATS = deque(maxlen=150)
_USERS: dict = {}   # uid -> {name, count, last}

try:  # بارگذاری از دیسک در بوت
    with open(_FILE, encoding="utf-8") as _f:
        _d = json.load(_f)
    _CHATS.extend(_d.get("chats") or [])
    _USERS.update(_d.get("users") or {})
except Exception:  # noqa: BLE001
    pass


def _save():
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"chats": list(_CHATS), "users": _USERS}, f, ensure_ascii=False)
        os.replace(tmp, _FILE)
    except Exception:  # noqa: BLE001
        pass


def _now():
    try:
        import clock
        return clock.tehran_now().strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        import time
        return time.strftime("%m-%d %H:%M")


def record(uid, name, user_msg, bot_reply):
    try:
        _CHATS.appendleft({
            "t": _now(), "uid": str(uid or ""), "name": (name or "").strip(),
            "msg": (user_msg or "")[:220], "reply": (bot_reply or "")[:340],
        })
        u = _USERS.setdefault(str(uid or ""), {"name": "", "count": 0, "last": ""})
        u["name"] = (name or "").strip() or u.get("name", "")
        u["count"] = int(u.get("count", 0)) + 1
        u["last"] = _now()
        _save()
    except Exception:  # noqa: BLE001
        pass


def snapshot():
    try:
        import clock
        today = clock.tehran_now().strftime("%m-%d")
    except Exception:  # noqa: BLE001
        import time
        today = time.strftime("%m-%d")
    today_count = sum(1 for c in _CHATS if (c.get("t") or "").startswith(today))
    top = sorted(({"uid": k, **v} for k, v in _USERS.items()),
                 key=lambda u: u.get("count", 0), reverse=True)[:25]
    return {
        "chats": list(_CHATS)[:70],
        "users_active": len(_USERS),
        "today": today_count,
        "buffered": len(_CHATS),
        "top_users": top,
    }
