"""مدلِ چت و مدلِ عکس در زمانِ اجرا (از داشبورد قابلِ تغییر، بدونِ ری‌استارت).

مقدار در data/models.json ذخیره می‌شود؛ اگر خالی بود، به OPENAI_MODEL از .env برمی‌گردد.
"""
from __future__ import annotations

import json
import os

import config

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(_HERE, "data", "models.json")


def _load():
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _default():
    return (getattr(config, "OPENAI_MODEL", "") or "gpt-5.5").strip()


def chat_model():
    return (_load().get("chat") or _default()).strip()


def vision_model():
    d = _load()
    return (d.get("vision") or d.get("chat") or _default()).strip()


def analysis_model():
    """مدلِ اختصاصیِ «تحلیلِ فروش/مدیریتی» — پیش‌فرض gpt-5.5 (باهوش‌ترین، برای تحلیلِ عمیقِ روزانه)."""
    return (_load().get("analysis") or "gpt-5.5").strip()


def set_models(chat=None, vision=None, analysis=None):
    d = _load()
    if chat:
        d["chat"] = str(chat).strip()
    if vision:
        d["vision"] = str(vision).strip()
    if analysis:
        d["analysis"] = str(analysis).strip()
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, _PATH)
    return d
