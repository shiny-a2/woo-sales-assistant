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
    """آزمونِ A/B لحنِ مغز: ('A'|'B', متنِ اضافهٔ پرسونا). خاموش یا بدونِ واریانتِ B → ('', '').

    تخصیصِ پایدار بر اساسِ هشِ کاربر (هر کاربر همیشه در همان گروه) تا نتیجه معتبر باشد.
    """
    cfg = load()
    if not cfg.get("ab_enabled"):
        return "", ""
    b = (cfg.get("ab_variant_b") or "").strip()
    if not b:
        return "", ""
    import hashlib
    h = int(hashlib.md5(str(user_key or "x").encode("utf-8")).hexdigest(), 16)
    if h % 2 == 0:
        return "A", (cfg.get("ab_variant_a") or "").strip()
    return "B", b


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
