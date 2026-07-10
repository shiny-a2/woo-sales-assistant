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
_OLD_SNAPSHOT: list = []   # پوششِ قبلی حینِ سینکِ کامل (تا قطعِ وسطِ راه، ایندکس را کوچک نکند)


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


async def _crawl(params, acc, seen_ids, max_pages=600):   # سقف ۳۰هزار — کاتالوگِ واقعی ۲۵هزار+ است
    """یک پیمایشِ صفحه‌به‌صفحه با پارامترهای داده‌شده؛ نتایج به acc اضافه و ذخیرهٔ افزایشی می‌شود."""
    global _INDEX
    import asyncio

    import woo
    page = 1
    per = 50   # صفحهٔ سبک‌تر: هاستِ کند پاسخِ بزرگ را قطره‌ای می‌دهد و read-timeout را دور می‌زند
    while page <= max_pages:
        try:
            # تایم‌اوتِ سختِ هر صفحه — تا یک پاسخِ قطره‌ای کلِ همگام‌سازی را ساعت‌ها قفل نکند
            rows = await asyncio.wait_for(
                woo.get("products", {"per_page": per, "page": page, "status": "publish", **params}), timeout=120)
        except asyncio.TimeoutError:
            _META["last_error"] = f"page {page} timeout"
            print(f"[productindex] صفحهٔ {page} تایم‌اوت شد — ادامه در دورِ بعد")
            return False
        if not isinstance(rows, list) or not rows:
            return True
        for p in rows:
            if p.get("id") in seen_ids:
                continue
            seen_ids.add(p.get("id"))
            acc.append(_slim(p))
        # ذخیرهٔ افزایشی؛ ⚠️ حینِ پیمایش، آیتم‌های قدیمیِ هنوز-نرسیده هم حفظ شوند تا اگر سینک وسطِ راه
        # قطع شد، پوششِ جستجو «کوچک» نشود (تازه‌ها اول، قدیمی‌های باقی‌مانده تهِ فهرست)
        _INDEX = acc + [p for p in _OLD_SNAPSHOT if p.get("id") not in seen_ids]
        _META.update({"synced_at": time.time(), "count": len(_INDEX), "last_error": ""})
        _save()
        if page % 10 == 0:
            print(f"[productindex] {len(acc)} محصول تا صفحهٔ {page}")
        if len(rows) < per:
            return True
        page += 1
    return True


async def sync(force=False):
    """کلِ کاتالوگ را محلی ذخیره کن — **اولویت با موجودها** (اول instock تا جستجو سریع‌تر مفید شود)."""
    global _INDEX
    if _META.get("syncing"):
        return {"ok": False, "error": "already syncing"}
    _META["syncing"] = True
    t0 = time.time()
    try:
        global _OLD_SNAPSHOT
        _OLD_SNAPSHOT = list(_INDEX)   # پوششِ فعلی حینِ پیمایش حفظ شود
        _META["full_incomplete"] = True   # اگر وسطِ راه کشته شدیم، بعد از بوت ادامه داده شود
        _save()
        acc = []
        seen = set()
        # پاسِ ۱: فقط موجودها (اولویتِ فروش) — سریع‌تر در دسترسِ جستجو قرار می‌گیرند
        ok1 = await _crawl({"stock_status": "instock"}, acc, seen)
        print(f"[productindex] پاسِ موجودها تمام شد: {len(acc)}")
        # پاسِ ۲: بقیهٔ کاتالوگ (ناموجودها) — برای شناساییِ کد/رفرنس و پیشنهادِ مشابه لازم‌اند
        ok2 = await _crawl({}, acc, seen)
        if ok1 and ok2:
            _INDEX = acc               # پیمایش کامل شد → فقط دادهٔ تازه (حذف‌شده‌ها پاک می‌شوند)
            _META["full_at"] = time.time()
            _META["full_incomplete"] = False
            _META["count"] = len(_INDEX)
        _OLD_SNAPSHOT = []
        _META["duration"] = round(time.time() - t0, 1)
        _save()
        return {"ok": bool(acc), "count": len(_INDEX), "duration": _META["duration"], "complete": bool(ok1 and ok2)}
    except Exception as e:  # noqa: BLE001
        _META["last_error"] = str(e)
        return {"ok": False, "error": str(e)}
    finally:
        _META["syncing"] = False


async def sync_incremental():
    """به‌روزرسانیِ سریع: فقط محصولاتِ «تغییرکرده» از آخرین همگام‌سازی (موجودی/قیمت/ناموجودشدن و …).

    به‌جای پیمایشِ کلِ کاتالوگ (ده‌ها دقیقه)، با modified_after فقط تغییرات را می‌گیرد (ثانیه‌ای).
    """
    global _INDEX
    if _META.get("syncing"):
        return {"ok": False, "error": "already syncing"}
    since = float(_META.get("synced_at", 0) or 0)
    if not _INDEX or not since:
        return await sync()
    _META["syncing"] = True
    t0 = time.time()
    try:
        import asyncio

        import woo
        iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(since - 300))   # ۵ دقیقه هم‌پوشانیِ ایمن
        changed = []
        page = 1
        while page <= 20:   # تغییراتِ یک بازهٔ چندساعته به‌ندرت از ۱۰۰۰ می‌گذرد
            try:
                rows = await asyncio.wait_for(
                    woo.get("products", {"per_page": 50, "page": page, "status": "publish",
                                         "modified_after": iso, "orderby": "modified", "order": "desc"}),
                    timeout=120)
            except asyncio.TimeoutError:
                _META["last_error"] = "incremental timeout"
                break
            if not isinstance(rows, list) or not rows:
                break
            changed.extend(rows)
            if len(rows) < 50:
                break
            page += 1
        if changed:
            by_id = {p.get("id"): i for i, p in enumerate(_INDEX)}
            added = updated = 0
            for p in changed:
                s = _slim(p)
                i = by_id.get(s["id"])
                if i is None:
                    _INDEX.insert(0, s)   # محصولِ جدید → اولِ ایندکس (اولویتِ تازه‌ها)
                    added += 1
                else:
                    _INDEX[i] = s          # قیمت/موجودی/مشخصاتِ تازه
                    updated += 1
            _META.update({"count": len(_INDEX), "last_error": ""})
        _META["synced_at"] = time.time()
        _META["inc_at"] = time.time()
        _META["duration"] = round(time.time() - t0, 1)
        _save()
        n = len(changed)
        print(f"[productindex] به‌روزرسانیِ افزایشی: {n} تغییر در {_META['duration']}s")
        return {"ok": True, "changed": n, "duration": _META["duration"]}
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
