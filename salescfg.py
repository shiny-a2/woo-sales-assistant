"""تنظیماتِ فروشِ قابلِ‌ویرایش از داشبورد (بدونِ ری‌استارت) — مثلِ کدِ تخفیفِ «خریدِ اول».

در data/sales_settings.json ذخیره می‌شود.
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(_HERE, "data", "sales_settings.json")


def load():
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


def get(key, default=""):
    v = load().get(key, default)
    return v if v is not None else default


def ab_assign(user_key):
    """آزمونِ شخصیت/لحنِ مغز (A/B/C): ('A'|'B'|'C', متنِ شخصیت). خاموش یا بدونِ متن → ('', '').

    تخصیصِ پایدار بر اساسِ هشِ کاربر: هر کاربر همیشه همان شخصیت را می‌بیند (نامحسوس و معتبر برای آنالیز).
    فقط شخصیت‌هایی که متن دارند در چرخش‌اند؛ خودِ متن‌ها باید تصریح کنند که قوانینِ پایه مقدم‌اند.
    """
    cfg = load()
    if not cfg.get("ab_enabled"):
        return "", ""
    variants = [(k, (cfg.get(f"ab_variant_{k.lower()}") or "").strip()) for k in ("A", "B", "C")]
    active = [(k, v) for k, v in variants if v]
    if not active:
        return "", ""
    import hashlib
    h = int(hashlib.md5(str(user_key or "x").encode("utf-8")).hexdigest(), 16)
    return active[h % len(active)]


def set_many(**kw):
    d = load()
    for k, v in kw.items():
        if v is not None:
            d[k] = v
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _PATH)
    except Exception:  # noqa: BLE001
        pass
    return d
