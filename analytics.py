"""تحلیلِ رفتار و خواستهٔ مشتری برای «فروشِ بهتر» (فاز ۱ — پایه، قابلِ توسعه).

از هر گفتگو سیگنال‌های سبک می‌گیرد (بدونِ فراخوانیِ اضافهٔ LLM → ارزان و سریع) و تجمیع می‌کند:
  • محصول/برندِ موردِعلاقه (از کارت‌های نشان‌داده‌شده + متنِ مشتری)
  • سیگنالِ خرید (قیمت/سفارش/آدرس/پرداخت/اقساط …)
  • اعتراض و نقطهٔ ریزش (گرونه/فکر می‌کنم/بعداً …)
  • حال‌وهوا/رضایت (لحن)
و «مشتریانِ داغ» (سیگنالِ خریدِ بالا، هنوز سفارش نداده) را نگه می‌دارد.
خروجی از /api/brain/analytics برای داشبورد. داده در data/analytics.json (تا با ری‌استارت نماند صفر نشود).
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_HERE, "data", "analytics.json")

# برندهای رایجِ گالری (برای تشخیصِ برندِ موردِعلاقه از متن/نامِ محصول) — قابلِ توسعه
_BRANDS = ["پلیس", "کاسیو", "سیتیزن", "امگا", "سواچ", "ادوکس", "اوماکس", "ولدر", "فری لوک", "دنیل کلین",
           "آلبرت ترایس", "دیس کایک", "تیسو", "سیکو", "اورینت", "نیوی فورس", "کورن", "رومانسون"]
_BUY = ["قیمت", "چنده", "چند", "سفارش", "بخرم", "می‌خرم", "میخرم", "آدرس", "پرداخت", "کارت به کارت",
        "کارت‌به‌کارت", "اقساط", "قسط", "موجوده", "موجود", "ارسال", "خرید", "کد تخفیف", "نهایی",
        "ثبت سفارش", "شماره کارت", "شبا", "فیش", "واریز", "پیک", "پستی"]
_OBJ = ["گرون", "گران", "فکر می‌کنم", "فکر کنم", "بعدا", "بعداً", "نمی‌خوام", "نمیخوام", "پشیمون",
        "زیاده", "بودجه‌م", "بودجه ام", "ارزون‌تر", "ارزونتر", "تخفیف بده", "نه ممنون", "فعلا نه"]
_POS = ["ممنون", "مرسی", "عالی", "خوبه", "لطف", "دمت گرم", "محشره", "قشنگه", "عاشق"]
_NEG = ["ناراضی", "افتضاح", "کلافه", "مزخرف", "خسته شدم", "چرا جواب", "بد بود", "مسخره"]

_M: dict = {"day": "", "events": [], "agg": {}, "leads": {}, "totals": {}}
_MAX_EVENTS = 300
_MAX_LEADS = 80


def _today():
    try:
        import clock
        return clock.tehran_now().strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return time.strftime("%Y-%m-%d")


def _now():
    try:
        import clock
        return clock.tehran_now().strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return time.strftime("%m-%d %H:%M")


def _norm(s):
    return (s or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))


def _hits(text, words):
    return [w for w in words if w in text]


def _load():
    global _M
    try:
        with open(_FILE, encoding="utf-8") as f:
            _M = json.load(f)
    except Exception:  # noqa: BLE001
        _M = {"day": _today(), "events": [], "agg": {}, "leads": {}, "totals": {}}
    for k, d in (("events", []), ("agg", {}), ("leads", {}), ("totals", {})):
        _M.setdefault(k, d)


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
    if _M.get("day") != d:  # ریستِ روزانه (رویدادها/تجمیعِ امروز)، ولی totals و leads می‌مانند
        _M["day"] = d
        _M["events"] = []
        _M["agg"] = {}


def _inc(bucket, key, n=1):
    b = _M["agg"].setdefault(bucket, {})
    b[key] = b.get(key, 0) + n
    t = _M["totals"].setdefault(bucket, {})
    t[key] = t.get(key, 0) + n


def record(channel, cid, name, user_msg, ctx):
    """یک گفتگو را تحلیل و ثبت کن. از هوکِ پاسخِ مغز صدا زده می‌شود (سبک)."""
    try:
        _roll()
        ctx = ctx or {}
        text = _norm(user_msg or "").lower()
        if not text and not ctx.get("cards"):
            return
        buy = _hits(text, _BUY)
        obj = _hits(text, _OBJ)
        pos = _hits(text, _POS)
        neg = _hits(text, _NEG)
        order = bool(ctx.get("order"))
        # «محصولِ پرتقاضا» فقط از خواسته/ری‌اکشنِ خودِ کاربر شمرده می‌شود، نه هر کارتی که ما نشان دادیم:
        #   ریپلای/اشاره به کارتِ یک محصول، درخواستِ عکسِ روی مچِ همان، یا ثبتِ سفارشش.
        products = []
        rp = (ctx.get("reacted_product") or "").strip()
        if rp:
            products.append(rp)
        wm = ctx.get("wrist_media") or {}
        if isinstance(wm, dict) and (wm.get("product_name") or "").strip():
            products.append(wm["product_name"].strip())
        od = ctx.get("order") or {}
        if isinstance(od, dict) and (od.get("product") or "").strip():
            products.append(od["product"].strip())
        products = list(dict.fromkeys(products))[:5]
        # برند: فقط از متنِ خودِ کاربر (با تشخیصِ نام‌های مستعار/انگلیسی)
        try:
            import brands as _brmod
            brands = _brmod.find_in_text(user_msg or "")
        except Exception:  # noqa: BLE001
            brands = [b for b in _BRANDS if b.lower() in text]

        level = "high" if (order or len(buy) >= 2) else ("med" if buy else "low")
        sentiment = "pos" if len(pos) > len(neg) else ("neg" if len(neg) > len(pos) else "neu")

        # تجمیع
        _inc("intent", level)
        _inc("sentiment", sentiment)
        if obj:
            _inc("intent", "objection")
        if order:
            _inc("intent", "order")
        for p in products:
            _inc("products", p)
        for b in brands:
            _inc("brands", b)

        # رویدادِ اخیر (برای مرور)
        _M["events"].insert(0, {
            "t": _now(), "ch": channel or "", "cid": str(cid or ""), "name": (name or "").strip(),
            "msg": (user_msg or "")[:140], "intent": level, "sent": sentiment,
            "buy": len(buy), "obj": len(obj), "order": int(order),
            "products": products[:3], "brands": brands[:3],
        })
        del _M["events"][_MAX_EVENTS:]

        # مشتریِ داغ: سیگنالِ خرید دارد و هنوز سفارش نداده (score تجمعی)
        if cid:
            k = f"{channel}:{cid}"
            lead = _M["leads"].get(k, {"cid": str(cid), "ch": channel or "", "name": "", "score": 0,
                                       "orders": 0, "last_msg": "", "last_t": "", "products": []})
            lead["name"] = (name or "").strip() or lead.get("name", "")
            lead["score"] = int(lead.get("score", 0)) + len(buy) * 2 + (3 if order else 0) - len(obj)
            lead["orders"] = int(lead.get("orders", 0)) + int(order)
            lead["last_msg"] = (user_msg or "")[:120]
            lead["last_t"] = _now()
            for p in products:
                if p not in lead["products"]:
                    lead["products"].append(p)
            lead["products"] = lead["products"][:6]
            _M["leads"][k] = lead
            # نگه‌داشتنِ فقط داغ‌ترین‌ها
            if len(_M["leads"]) > _MAX_LEADS * 2:
                top = sorted(_M["leads"].items(), key=lambda kv: kv[1].get("score", 0), reverse=True)[:_MAX_LEADS]
                _M["leads"] = dict(top)
        _save()
    except Exception:  # noqa: BLE001
        pass


HOT_ALERT_THRESHOLD = 6  # امتیازِ خرید که «داغ» حساب می‌شود (≈ ۳ سیگنالِ خریدِ تجمعی)


def pop_new_hot_leads(threshold=HOT_ALERT_THRESHOLD):
    """لیدهایی که تازه به آستانهٔ «داغ» رسیده‌اند و هنوز هشدارشان نرفته — یک‌بار برمی‌گرداند و علامت می‌زند.

    از حلقهٔ هشدارِ web_server صدا زده می‌شود تا به گروهِ پیگیری/CRM خبر بدهد.
    """
    try:
        _roll()
        out = []
        changed = False
        for l in _M["leads"].values():
            if l.get("orders", 0) == 0 and l.get("score", 0) >= threshold and not l.get("alerted"):
                l["alerted"] = True
                changed = True
                out.append(dict(l))
        if changed:
            _save()
        return out
    except Exception:  # noqa: BLE001
        return []


def _topn(bucket, n=10):
    d = _M["agg"].get(bucket, {})
    return sorted(({"name": k, "count": v} for k, v in d.items()), key=lambda x: x["count"], reverse=True)[:n]


def snapshot():
    _roll()
    # مشتریانِ داغ: بالاترین score، هنوز سفارش‌نداده اول
    leads = sorted(_M["leads"].values(), key=lambda l: (l.get("orders", 0) == 0, l.get("score", 0)), reverse=True)
    hot = [l for l in leads if l.get("score", 0) >= 2 and l.get("orders", 0) == 0][:25]
    intent = _M["agg"].get("intent", {})
    return {
        "day": _M.get("day"),
        "top_products": _topn("products", 12),
        "top_brands": _topn("brands", 12),
        "intent": {"high": intent.get("high", 0), "med": intent.get("med", 0), "low": intent.get("low", 0),
                   "objection": intent.get("objection", 0), "order": intent.get("order", 0)},
        "sentiment": _M["agg"].get("sentiment", {}),
        "hot_leads": hot,
        "recent": _M["events"][:40],
        "totals": {"products": len(_M["totals"].get("products", {})),
                   "brands": _M["totals"].get("brands", {}),
                   "intent": _M["totals"].get("intent", {})},
    }


_load()
_roll()
