"""ایندکسِ محلیِ محصولات — کلِ کاتالوگ را در پس‌زمینه از ووکامرس می‌گیرد و محلی نگه می‌دارد،
تا «جستجوی محصول در چت آنی و بدونِ وابستگی به هاستِ کندِ سایت» باشد.

- همگام‌سازیِ دوره‌ای (پیش‌فرض هر ۸ ساعت؛ از داشبورد قابلِ تنظیم) + دکمهٔ «به‌روزرسانیِ الان».
- دادهٔ کاملِ محصول ذخیره می‌شود (ویژگی‌ها، تگ‌ها، توضیحات، برند…) — پایهٔ دانشِ محصول و APIِ آینده.
- خواندنِ سریع با درخواستِ کمتر: per_page=100 و فقط تا وقتی صفحه پر است.
داده در data/products_index.json.
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_HERE, "data", "products_index.json")

_INDEX: list = []
_META: dict = {"synced_at": 0.0, "count": 0, "syncing": False, "last_error": "", "duration": 0}


def _slim(p):
    """فیلدهای مفیدِ محصول را نگه دار (بدونِ بلوتِ _links/meta_data)؛ ساختارِ ووکامرس حفظ می‌شود
    تا کمک‌تابع‌های woo (_product_brief/attr_options) بی‌تغییر روی آن کار کنند."""
    return {
        "id": p.get("id"),
        "name": p.get("name") or "",
        "slug": p.get("slug") or "",
        "sku": p.get("sku") or "",
        "permalink": p.get("permalink") or "",
        "type": p.get("type") or "",
        "status": p.get("status") or "",
        "price": p.get("price") or "",
        "regular_price": p.get("regular_price") or "",
        "sale_price": p.get("sale_price") or "",
        "on_sale": bool(p.get("on_sale")),
        "purchasable": bool(p.get("purchasable", True)),
        "stock_status": p.get("stock_status") or "",
        "manage_stock": bool(p.get("manage_stock")),   # لازم برای availability(): فروشگاه (ارسال فوری) vs شرکت (۳-۷ روز)
        "stock_quantity": p.get("stock_quantity"),
        "featured": bool(p.get("featured")),
        "average_rating": p.get("average_rating") or "",
        "categories": [{"id": c.get("id"), "name": c.get("name")} for c in (p.get("categories") or [])],
        "tags": [{"id": t.get("id"), "name": t.get("name")} for t in (p.get("tags") or [])],
        "attributes": [{"name": a.get("name"), "options": a.get("options") or []} for a in (p.get("attributes") or [])],
        "images": [{"src": (im or {}).get("src", "")} for im in (p.get("images") or [])[:2]],
        "description": (p.get("description") or "")[:4000],
        "short_description": (p.get("short_description") or "")[:800],
        "date_created": (p.get("date_created") or ""),
        "date_modified": (p.get("date_modified") or ""),
    }


def _load():
    global _INDEX, _META
    try:
        with open(_FILE, encoding="utf-8") as f:
            d = json.load(f)
        _INDEX = d.get("products") or []
        _META.update(d.get("meta") or {})
        _META["count"] = len(_INDEX)
    except Exception:  # noqa: BLE001
        _INDEX = []
    _META["syncing"] = False   # فلگِ دیسک از پراسسِ قبلی معتبر نیست؛ وگرنه سینک برای همیشه قفل می‌مانَد


def _save():
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"meta": _META, "products": _INDEX}, f, ensure_ascii=False)
        os.replace(tmp, _FILE)
    except Exception as e:  # noqa: BLE001
        _META["last_error"] = f"save: {e}"


def refresh_hours():
    try:
        import salescfg
        h = float(salescfg.get("product_sync_hours", 8) or 8)
        return max(0.25, min(h, 168))
    except Exception:  # noqa: BLE001
        return 8.0


def is_fresh():
    """آیا ایندکس قابلِ‌استفاده است؟ داده دارد و خیلی کهنه نیست (وگرنه از سایتِ زنده بخوان)."""
    if not _INDEX:
        return False
    max_age = max(24 * 3600, refresh_hours() * 3 * 3600)
    return (time.time() - float(_META.get("synced_at", 0))) < max_age


def all_products():
    return _INDEX


def count():
    return len(_INDEX)


def get_product(pid):
    for p in _INDEX:
        if str(p.get("id")) == str(pid):
            return p
    return None


async def sync(force=False):
    """کلِ کاتالوگِ منتشرشده را صفحه‌به‌صفحه بگیر و محلی ذخیره کن (پس‌زمینه)."""
    global _INDEX
    if _META.get("syncing"):
        return {"ok": False, "error": "already syncing"}
    _META["syncing"] = True
    t0 = time.time()
    try:
        import asyncio

        import woo
        acc = []
        page = 1
        per = 50   # صفحهٔ سبک‌تر: هاستِ کند پاسخِ بزرگ را قطره‌ای می‌دهد و read-timeout را دور می‌زند
        while page <= 300:   # سقفِ ایمنی (۱۵٬۰۰۰ محصول — کاتالوگ از ۶۰۰۰ بزرگ‌تر درآمد)
            try:
                # تایم‌اوتِ سختِ هر صفحه — تا یک پاسخِ قطره‌ای کلِ همگام‌سازی را ساعت‌ها قفل نکند
                rows = await asyncio.wait_for(
                    woo.get("products", {"per_page": per, "page": page, "status": "publish"}), timeout=120)
            except asyncio.TimeoutError:
                _META["last_error"] = f"page {page} timeout"
                print(f"[productindex] صفحهٔ {page} تایم‌اوت شد — ادامه در دورِ بعد")
                break
            if not isinstance(rows, list) or not rows:
                break
            acc.extend(_slim(p) for p in rows)
            _INDEX = acc      # ذخیرهٔ افزایشی: جستجو زودتر داده دارد و ری‌استارت پیشرفت را نمی‌بازد
            _META.update({"synced_at": time.time(), "count": len(acc), "last_error": ""})
            _save()
            if page % 10 == 0:
                print(f"[productindex] {len(acc)} محصول تا صفحهٔ {page}")
            if len(rows) < per:
                break
            page += 1
        _META["duration"] = round(time.time() - t0, 1)
        _save()
        return {"ok": bool(acc), "count": len(acc), "duration": _META["duration"]}
    except Exception as e:  # noqa: BLE001
        _META["last_error"] = str(e)
        return {"ok": False, "error": str(e)}
    finally:
        _META["syncing"] = False


def status():
    now = time.time()
    age = now - float(_META.get("synced_at", 0)) if _META.get("synced_at") else None
    rh = refresh_hours()
    return {
        "count": len(_INDEX),
        "synced_at": _META.get("synced_at", 0),
        "age_min": round(age / 60, 1) if age is not None else None,
        "syncing": bool(_META.get("syncing")),
        "last_error": _META.get("last_error", ""),
        "duration": _META.get("duration", 0),
        "refresh_hours": rh,
        "fresh": is_fresh(),
        "next_in_min": round(max(0, rh * 3600 - (age or 0)) / 60, 1) if age is not None else 0,
    }


_load()
