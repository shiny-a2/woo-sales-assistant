"""ایندکسِ هویتِ مشتری در همهٔ کانال‌ها + سابقهٔ خرید (کش‌شده).

هدف: مغز بداند هر مخاطب (واتساپ/اینستاگرام/تلگرام/سایت) کیست، شماره‌اش چیست، آیدیِ اینستایش به
کدام مشتری وصل است، و چه خریده — تا شخصی‌سازی‌شده و دقیق رفتار کند. داده در data/crm_index.json.
جستجوی سابقه فقط هر REFRESH_H ساعت یک‌بار به سایت می‌زند (مدیریتِ ریکوئست/بار).
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_HERE, "data", "crm_index.json")
REFRESH_H = 12

_M: dict = {}


def _digits(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _load():
    global _M
    try:
        with open(_FILE, encoding="utf-8") as f:
            _M = json.load(f) or {}
    except Exception:  # noqa: BLE001
        _M = {}


def _save():
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_M, f, ensure_ascii=False)
        os.replace(tmp, _FILE)
    except Exception:  # noqa: BLE001
        pass


def _key(channel, cid):
    return f"{channel or 'ch'}:{cid or ''}"


def get(channel, cid):
    return _M.get(_key(channel, cid)) or {}


def link(channel, cid, phone=None, name=None, ig_id=None):
    """هویتِ یک مخاطب را ثبت/به‌روز کن (شماره، نام، آیدیِ اینستا)."""
    e = _M.setdefault(_key(channel, cid), {"channel": channel, "cid": str(cid or "")})
    if phone:
        e["phone"] = _digits(phone)
    if name:
        e["name"] = name
    if ig_id:
        e["ig_id"] = str(ig_id)
    _save()
    return e


def touch(channel, cid, name=None):
    """هویتِ یک مخاطب را در ایندکس ثبت کن (بدونِ زدن به سایت) — «افزودن به کانتکت‌ها».

    برای همهٔ کانال‌ها (به‌ویژه اینستاگرام/تلگرام که شماره ندارند) تا نقشهٔ هویت ساخته شود.
    اتصال به سفارش‌های سایت فقط زمانی رخ می‌دهد که «شماره» معلوم شود (امن؛ بدونِ تطبیقِ حدسیِ نام).
    """
    if not cid:
        return
    k = _key(channel, cid)
    new = k not in _M
    e = _M.setdefault(k, {"channel": channel, "cid": str(cid)})
    changed = new
    if name and e.get("name") != name:
        e["name"] = name
        changed = True
    if changed:
        _save()


def by_phone(phone):
    p = _digits(phone)
    if len(p) < 9:
        return []
    return [e for e in _M.values() if e.get("phone", "").endswith(p[-9:])]


def _fmt_hint(entry):
    orders = entry.get("orders") or []
    if not orders:
        return ""   # مشتریِ جدید یا بدونِ سابقهٔ خریدِ یافت‌شده
    paid = [o for o in orders if o.get("status") in ("completed", "processing", "on-hold")]
    lines = []
    for o in orders[:4]:
        items = "، ".join(o.get("items") or [])[:80]
        lines.append(f"- سفارش {o.get('number')} ({(o.get('date') or '')[:10]}, {o.get('status')}): {items}")
    return ("🧾 سابقهٔ خریدِ این مشتری (فقط برای شخصی‌سازیِ تو؛ خودت بی‌جهت لو نده): "
            + ("مشتریِ قدیمیِ ماست. " if paid else "سفارش‌هایی داشته (شاید ناتمام). ")
            + "\n" + "\n".join(lines)
            + "\n→ گرم و شخصی رفتار کن؛ اگر سفارشی ناتمام مانده، محترمانه کمک کن کامل شود.")


async def history_hint(channel, cid, phone):
    """راهنمای سابقهٔ خرید برای تزریق به پرامپت (کش‌شده؛ فقط هر REFRESH_H ساعت به سایت می‌زند).

    سینکِ بین‌کانالی: با معلوم‌شدنِ «شماره»، هویتِ همان مشتری در کانال‌های دیگر (واتساپ/اینستا/تلگرام/سایت)
    به هم وصل می‌شود — سابقهٔ خرید و نام بینِ همهٔ ورودی‌های هم‌شماره مشترک و هم‌زمان می‌ماند.
    """
    if not phone:
        return ""
    e = _M.setdefault(_key(channel, cid), {"channel": channel, "cid": str(cid or "")})
    e["phone"] = _digits(phone)
    now = time.time()
    if "orders" not in e or (now - float(e.get("checked_at", 0))) > REFRESH_H * 3600:
        # اول از هویت‌های هم‌شماره در کانال‌های دیگر (اگر تازه دارند) — بدونِ زدن به سایت
        fresh = None
        for o in by_phone(phone):
            if o is not e and "orders" in o and (now - float(o.get("checked_at", 0))) < REFRESH_H * 3600:
                fresh = o
                break
        if fresh:
            e["orders"] = fresh["orders"]
            e["checked_at"] = fresh.get("checked_at", now)
        else:
            try:
                import woo
                e["orders"] = await woo.customer_orders(phone)
                e["checked_at"] = now
            except Exception:  # noqa: BLE001
                e.setdefault("orders", [])
        # انتشارِ سابقه و نام به همهٔ هویت‌های هم‌شمارهٔ این مشتری (سینکِ بین‌کانالی)
        try:
            for o in by_phone(phone):
                if o is not e:
                    o["orders"] = e.get("orders", [])
                    o["checked_at"] = e.get("checked_at", now)
                    if e.get("name") and not o.get("name"):
                        o["name"] = e["name"]
                    if o.get("name") and not e.get("name"):
                        e["name"] = o["name"]
        except Exception:  # noqa: BLE001
            pass
        _save()
    return _fmt_hint(e)


_load()
