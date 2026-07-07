"""DNA مشتری: پروفایلِ واحدِ هر مشتری در همهٔ کانال‌ها + رفتار/علایق + سابقهٔ خرید (CRM).

هدف: مغز هر مخاطب را «بهتر از خودش» بشناسد. هر ورودی از هر کانالی (واتساپ/اینستاگرام/تلگرام/سایت)
یک «هویت» می‌سازد؛ همهٔ هویت‌های یک نفر زیرِ یک «پروفایلِ واحد (person/DNA)» جمع می‌شوند. به‌محضِ
معلوم‌شدنِ «شماره»، هویت‌های هم‌شماره (مثلاً کانتکتِ اینستاگرامیِ بی‌شماره) در همان پروفایل ادغام و در
همهٔ کانال‌ها سینک می‌شوند. رفتار (برند/محصولِ موردِعلاقه، سیگنالِ خرید، اعتراض، تعمیر/فروش/ارجاعِ حضوری،
لحنِ برنده) و سابقهٔ خریدِ سایت داخلِ همین پروفایل جمع می‌شود.

داده در data/crm_index.json (سازگارِ عقب‌رو با نسخهٔ فلتِ قدیمی). سابقهٔ سایت فقط هر REFRESH_H ساعت
یک‌بار استعلام می‌شود (مدیریتِ بار/ریکوئست).
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_HERE, "data", "crm_index.json")
REFRESH_H = 12

_M: dict = {}   # "channel:cid" -> identity: {channel, cid, phone, name, ig_id, pid, orders, checked_at}
_P: dict = {}   # pid -> person profile (DNA)
_CRAWL: dict = {"running": False, "last": 0, "phones": 0, "orders": 0, "error": ""}   # وضعیتِ خزندهٔ backfill

_PAID = ("completed", "processing", "on-hold", "deliver", "delivered")


def _digits(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _same_phone(a, b):
    a, b = _digits(a), _digits(b)
    return bool(a) and bool(b) and len(a) >= 9 and len(b) >= 9 and a[-9:] == b[-9:]


def _key(channel, cid):
    return f"{channel or 'ch'}:{cid or ''}"


def _now():
    return time.time()


def _top(counter, n):
    if not counter:
        return []
    return [k for k, _ in sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:n]]


def _merge_counts(dst, src):
    for k, v in (src or {}).items():
        try:
            dst[k] = dst.get(k, 0) + v
        except Exception:  # noqa: BLE001
            dst[k] = v


# ---------------- ماندگاری ----------------
def _load():
    global _M, _P
    try:
        with open(_FILE, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:  # noqa: BLE001
        data = {}
    if isinstance(data, dict) and "identities" in data:  # نسخهٔ جدید
        _M = data.get("identities") or {}
        _P = data.get("persons") or {}
    else:  # نسخهٔ فلتِ قدیمی → مهاجرت
        _M = data if isinstance(data, dict) else {}
        _P = {}
        _rebuild_persons()
    if not _P:  # اطمینان: اگر پروفایلی نبود، از هویت‌ها بساز
        _rebuild_persons()


def _save():
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"identities": _M, "persons": _P}, f, ensure_ascii=False)
        os.replace(tmp, _FILE)
    except Exception:  # noqa: BLE001
        pass


def _rebuild_persons():
    """پروفایل‌ها را از روی هویت‌های موجود بازسازی/ادغام کن (برای بوت و مهاجرت)."""
    for e in list(_M.values()):
        e.pop("pid", None)
    for e in list(_M.values()):
        p = _person_for(e)
        if e.get("name"):
            _add_name(p, e["name"])
        if e.get("orders") is not None:
            _apply_crm(p, e.get("orders") or [])
        if e.get("phone"):
            _link_phone(e, e["phone"])


# ---------------- پروفایلِ واحد (person) ----------------
def _new_person(pid):
    now = _now()
    return {"pid": pid, "created_at": now, "updated_at": now, "name": "", "names": [],
            "phones": [], "identities": [], "channels": {}, "msgs": 0,
            "first_seen": now, "last_seen": now,
            "brands": {}, "products": {}, "intent": {}, "sentiment": {},
            "signals": {}, "tone": {}, "interests": [], "crm": {}, "notes": ""}


def _attach_identity(p, entry):
    ch, cid = entry.get("channel"), str(entry.get("cid") or "")
    for i in p["identities"]:
        if i.get("channel") == ch and str(i.get("cid")) == cid:
            if entry.get("ig_id") and not i.get("ig_id"):
                i["ig_id"] = str(entry["ig_id"])
            return
    p["identities"].append({"channel": ch, "cid": cid, "ig_id": str(entry.get("ig_id") or "")})


def _person_for(entry):
    pid = entry.get("pid")
    if pid and pid in _P:
        _attach_identity(_P[pid], entry)
        return _P[pid]
    pid = "p:" + _key(entry.get("channel"), entry.get("cid"))
    p = _P.get(pid) or _new_person(pid)
    _P[pid] = p
    entry["pid"] = pid
    _attach_identity(p, entry)
    return p


def _add_name(p, name):
    name = (name or "").strip()
    if not name:
        return
    if name not in p["names"]:
        p["names"].append(name)
    if not p.get("name"):
        p["name"] = name


def _merge_persons(keep_pid, drop_pid):
    if keep_pid == drop_pid:
        return keep_pid
    a, b = _P.get(keep_pid), _P.get(drop_pid)
    if not a or not b:
        return keep_pid
    for i in b.get("identities", []):
        if not any(x.get("channel") == i.get("channel") and str(x.get("cid")) == str(i.get("cid"))
                   for x in a["identities"]):
            a["identities"].append(i)
    for e in _M.values():          # هویت‌ها را به پروفایلِ نگه‌داشته وصل کن
        if e.get("pid") == drop_pid:
            e["pid"] = keep_pid
    a["phones"] = list(dict.fromkeys(a.get("phones", []) + b.get("phones", [])))
    for nm in b.get("names", []):
        _add_name(a, nm)
    if not a.get("name"):
        a["name"] = b.get("name", "")
    a["msgs"] = a.get("msgs", 0) + b.get("msgs", 0)
    a["first_seen"] = min(a.get("first_seen", _now()), b.get("first_seen", _now()))
    a["last_seen"] = max(a.get("last_seen", 0), b.get("last_seen", 0))
    for k in ("brands", "products", "intent", "sentiment", "signals", "tone", "channels"):
        _merge_counts(a.setdefault(k, {}), b.get(k, {}))
    a["interests"] = list(dict.fromkeys(a.get("interests", []) + b.get("interests", [])))
    if b.get("crm") and (not a.get("crm") or (b["crm"].get("orders_count", 0) > a["crm"].get("orders_count", 0))):
        a["crm"] = b["crm"]           # پروفایلی که سابقهٔ خریدِ بهتری دارد را نگه دار
    if b.get("notes") and not a.get("notes"):
        a["notes"] = b["notes"]
    _P.pop(drop_pid, None)
    return keep_pid


def _link_phone(entry, phone):
    """شماره را روی پروفایل ثبت و همهٔ پروفایل‌های هم‌شماره را در یکی ادغام کن (سینکِ بین‌کانالی)."""
    ph = _digits(phone)
    p = _person_for(entry)
    if ph and ph not in p["phones"]:
        p["phones"].append(ph)
    if len(ph) < 9:
        return p
    for e in list(_M.values()):
        if e is entry:
            continue
        if _same_phone(e.get("phone", ""), ph) or _same_phone("".join(e.get("_p2", "")), ph):
            op = _person_for(e)
            if op.get("phones") and not any(_same_phone(x, ph) for x in op["phones"]):
                continue
            if op["pid"] != p["pid"]:
                keep, drop = sorted([p["pid"], op["pid"]])
                _merge_persons(keep, drop)
                p = _P[keep]
    return p


def _apply_crm(p, orders):
    orders = orders or []
    if not orders:
        return
    paid = [o for o in orders if o.get("status") in _PAID]
    dates = sorted([(o.get("date") or "")[:10] for o in orders if o.get("date")])
    items = []
    for o in orders:
        items.extend(o.get("items") or [])
    fav = ""
    try:
        import brands as _br
        from collections import Counter
        allb = []
        for it in items:
            allb.extend(_br.find_in_text(it))
        if allb:
            fav = Counter(allb).most_common(1)[0][0]
    except Exception:  # noqa: BLE001
        pass
    p["crm"] = {"orders_count": len(orders), "paid_count": len(paid),
                "items": items[:12], "first_order": dates[0] if dates else "",
                "last_order": dates[-1] if dates else "", "fav_brand": fav}
    if fav:
        p["brands"][fav] = p["brands"].get(fav, 0) + 2


# ---------------- API عمومی (سازگارِ عقب‌رو) ----------------
def get(channel, cid):
    return _M.get(_key(channel, cid)) or {}


def link(channel, cid, phone=None, name=None, ig_id=None):
    """هویتِ یک مخاطب را ثبت/به‌روز کن (شماره، نام، آیدیِ اینستا) + پروفایلِ واحد."""
    e = _M.setdefault(_key(channel, cid), {"channel": channel, "cid": str(cid or "")})
    if ig_id:
        e["ig_id"] = str(ig_id)
    p = _person_for(e)
    if name:
        e["name"] = name
        _add_name(p, name)
    if phone:
        e["phone"] = _digits(phone)
        _link_phone(e, phone)
    _save()
    return e


def touch(channel, cid, name=None):
    """هویتِ مخاطب را در ایندکس ثبت کن (بدونِ زدن به سایت) — «افزودن به کانتکت‌ها» + ساختِ پروفایل."""
    if not cid:
        return
    e = _M.setdefault(_key(channel, cid), {"channel": channel, "cid": str(cid)})
    p = _person_for(e)
    changed = False
    if name and e.get("name") != name:
        e["name"] = name
        _add_name(p, name)
        changed = True
    _save() if changed else None


def observe(channel, cid, name=None, signals=None):
    """رفتارِ یک گفتگو را روی پروفایلِ DNA تجمیع کن (بعد از هر پاسخِ مغز صدا زده می‌شود)."""
    if not cid:
        return
    try:
        e = _M.setdefault(_key(channel, cid), {"channel": channel, "cid": str(cid)})
        if name and not e.get("name"):
            e["name"] = name
        p = _person_for(e)
        now = _now()
        p["last_seen"] = now
        p["msgs"] = p.get("msgs", 0) + 1
        p["channels"][channel or "ch"] = p["channels"].get(channel or "ch", 0) + 1
        if name:
            _add_name(p, name)
        s = signals or {}
        for b in (s.get("brands") or []):
            p["brands"][b] = p["brands"].get(b, 0) + 1
        for pr in (s.get("products") or []):
            p["products"][pr] = p["products"].get(pr, 0) + 2   # واکنش به محصول وزنِ بیشتر
        if s.get("level"):
            p["intent"][s["level"]] = p["intent"].get(s["level"], 0) + 1
        if s.get("order"):
            p["intent"]["order"] = p["intent"].get("order", 0) + 1
        if s.get("objection"):
            p["intent"]["objection"] = p["intent"].get("objection", 0) + 1
        if s.get("sentiment"):
            p["sentiment"][s["sentiment"]] = p["sentiment"].get(s["sentiment"], 0) + 1
        for sig in ("repair", "sell_intent", "store_referral"):
            if s.get(sig):
                p["signals"][sig] = p["signals"].get(sig, 0) + 1
        if s.get("ab"):
            p["tone"][s["ab"]] = p["tone"].get(s["ab"], 0) + 1
        p["updated_at"] = now
        _save()
    except Exception:  # noqa: BLE001
        pass


def by_phone(phone):
    p = _digits(phone)
    if len(p) < 9:
        return []
    return [e for e in _M.values() if _same_phone(e.get("phone", ""), p)]


# ---------------- سابقهٔ خرید + هینتِ مغز ----------------
def _fmt_history(entry):
    orders = entry.get("orders") or []
    if not orders:
        return ""
    paid = [o for o in orders if o.get("status") in _PAID]
    lines = []
    for o in orders[:4]:
        items = "، ".join(o.get("items") or [])[:80]
        lines.append(f"- سفارش {o.get('number')} ({(o.get('date') or '')[:10]}, {o.get('status')}): {items}")
    return ("🧾 سابقهٔ خریدِ این مشتری (فقط برای شخصی‌سازیِ تو؛ خودت بی‌جهت لو نده): "
            + ("مشتریِ قدیمیِ ماست. " if paid else "سفارش‌هایی داشته (شاید ناتمام). ")
            + "\n" + "\n".join(lines)
            + "\n→ گرم و شخصی رفتار کن؛ اگر سفارشی ناتمام مانده، محترمانه کمک کن کامل شود.")


async def history_hint(channel, cid, phone):
    """سابقهٔ خرید (کش‌شده) + سینکِ بین‌کانالی با شماره. خروجی برای تزریق به پرامپت."""
    if not phone:
        return ""
    e = _M.setdefault(_key(channel, cid), {"channel": channel, "cid": str(cid or "")})
    e["phone"] = _digits(phone)
    p = _link_phone(e, phone)   # ادغامِ هویت‌های هم‌شماره در یک پروفایل
    now = _now()
    if "orders" not in e or (now - float(e.get("checked_at", 0))) > REFRESH_H * 3600:
        fresh = None                       # اول از هویت‌های هم‌شمارهٔ تازه (بدونِ زدن به سایت)
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
        for o in by_phone(phone):          # انتشارِ سابقه به همهٔ هویت‌های هم‌شماره
            if o is not e:
                o["orders"] = e.get("orders", [])
                o["checked_at"] = e.get("checked_at", now)
        _apply_crm(p, e.get("orders") or [])
        _save()
    return _fmt_history(e)


def dna_hint(channel, cid):
    """پروفایلِ رفتاریِ مشتری (برندها/علایق/سیگنال‌ها/کانال‌ها/سابقه) برای تزریق به مغز.

    مستقل از شماره کار می‌کند (کاربرانِ فقط-اینستاگرامی هم DNA دارند)."""
    e = _M.get(_key(channel, cid))
    if not e:
        return ""
    p = _P.get(e.get("pid") or "")
    if not p or p.get("msgs", 0) < 1:
        return ""
    bits = []
    fav = _top(p.get("brands", {}), 3)
    if fav:
        bits.append("برندهایی که برایش جذاب بوده: " + "، ".join(fav))
    prods = _top(p.get("products", {}), 2)
    if prods:
        bits.append("محصولاتی که رویشان واکنش نشان داده: " + "، ".join(prods))
    intent = p.get("intent", {})
    if intent.get("order"):
        bits.append("قبلاً قصدِ خرید/سفارش داشته")
    elif intent.get("high", 0) + intent.get("med", 0) >= 2:
        bits.append("سیگنالِ خریدش بالاست")
    if intent.get("objection", 0) >= 2:
        bits.append("به قیمت حساس بوده (قبلاً تردید/اعتراضِ قیمتی داشته) — ارزش و گارانتی را آرام یادآوری کن")
    sig = p.get("signals", {})
    if sig.get("repair"):
        bits.append("سابقهٔ پرسشِ تعمیر داشته")
    if sig.get("sell_intent"):
        bits.append("قصدِ فروشِ ساعتِ خودش را داشته")
    if sig.get("store_referral"):
        bits.append("تمایل به مراجعهٔ حضوری نشان داده — مزایای خریدِ آنلاین را برجسته کن")
    crm = p.get("crm") or {}
    if crm.get("orders_count"):
        extra = f" (آخرین خرید: {crm.get('last_order')})" if crm.get("last_order") else ""
        bits.append(f"مشتریِ قدیمی با {crm['orders_count']} سفارش{extra}"
                    + (f"؛ برندِ خریدِ قبلی: {crm['fav_brand']}" if crm.get("fav_brand") else ""))
    chans = [c for c in (p.get("channels") or {}).keys()]
    if len(chans) > 1:
        bits.append("از چند کانالِ مختلف با ما در ارتباط بوده (همان شخص)")
    if not bits:
        return ""
    nm = p.get("name") or ""
    head = f"🧬 پروفایلِ این مشتری{(' («' + nm + '»)') if nm else ''} — فقط برای شخصی‌سازیِ تو (خودت بی‌جهت رو نکن): "
    return head + "؛ ".join(bits) + ". از این شناخت برای پیشنهادِ دقیق‌تر و لحنِ مناسب‌تر استفاده کن."


# ---------------- خروجی برای داشبورد ----------------
def _public_person(p, brief=False):
    out = {"pid": p.get("pid"), "name": p.get("name") or "",
           "phones": p.get("phones", []), "channels": list((p.get("channels") or {}).keys()),
           "msgs": p.get("msgs", 0), "fav_brands": _top(p.get("brands", {}), 3),
           "last_seen": p.get("last_seen", 0), "crm": p.get("crm", {}),
           "signals": p.get("signals", {}), "identities_count": len(p.get("identities", []))}
    if not brief:
        out.update({"products": _top(p.get("products", {}), 8), "brands_all": p.get("brands", {}),
                    "intent": p.get("intent", {}), "sentiment": p.get("sentiment", {}),
                    "tone": p.get("tone", {}), "identities": p.get("identities", []),
                    "names": p.get("names", []), "interests": p.get("interests", []),
                    "first_seen": p.get("first_seen", 0), "notes": p.get("notes", "")})
    return out


def profile(channel, cid):
    e = _M.get(_key(channel, cid))
    if not e:
        return {}
    p = _P.get(e.get("pid") or "")
    return _public_person(p) if p else {}


def get_profile(pid):
    p = _P.get(pid)
    return _public_person(p) if p else {}


def top_profiles(limit=60):
    ps = sorted(_P.values(), key=lambda p: p.get("last_seen", 0), reverse=True)[:limit]
    return [_public_person(p, brief=True) for p in ps]


def stats():
    total = len(_P)
    withphone = sum(1 for p in _P.values() if p.get("phones"))
    buyers = sum(1 for p in _P.values() if (p.get("crm") or {}).get("orders_count"))
    multichannel = sum(1 for p in _P.values() if len(p.get("channels") or {}) > 1)
    return {"persons": total, "with_phone": withphone, "buyers": buyers, "multichannel": multichannel,
            "identities": len(_M),
            "crawl": {"running": _CRAWL["running"], "last": _CRAWL["last"], "phones": _CRAWL["phones"],
                      "orders": _CRAWL["orders"], "error": _CRAWL["error"]}}


async def crawl_from_woo(max_orders=1500):
    """خزندهٔ backfill: از سفارش‌های موجودِ ووکامرس، پروفایلِ واحدِ DNA برای مشتری‌های قبلی می‌سازد.

    مشتری‌ها را بر اساسِ «شماره» گروه‌بندی می‌کند و برای هر شماره یک هویتِ seed (channel=site) +
    پروفایل با سابقهٔ خرید می‌سازد. بعداً وقتی همان مشتری از هر کانالی پیام داد و شماره‌اش معلوم شد،
    _link_phone او را در همین پروفایل ادغام و در همهٔ کانال‌ها سینک می‌کند.
    """
    if _CRAWL["running"]:
        return {"ok": False, "error": "already running"}
    _CRAWL["running"] = True
    _CRAWL["error"] = ""
    try:
        import woo
        groups = {}   # last9 → {phone, name, orders[]}
        page = 1
        processed = 0
        while processed < max_orders and page <= 60:
            try:
                rows = await woo.get("orders", {"per_page": 50, "page": page, "orderby": "date", "order": "desc"})
            except Exception as e:  # noqa: BLE001
                _CRAWL["error"] = f"woo page {page}: {e}"
                break
            if not isinstance(rows, list) or not rows:
                break
            for o in rows:
                billing = o.get("billing") or {}
                ph = _digits(billing.get("phone") or "")
                if len(ph) < 9:
                    continue
                g = groups.setdefault(ph[-9:], {"phone": ph, "name": "", "orders": []})
                nm = ((billing.get("first_name") or "") + " " + (billing.get("last_name") or "")).strip()
                if nm and not g["name"]:
                    g["name"] = nm
                g["orders"].append({
                    "number": o.get("number") or o.get("id"),
                    "date": (o.get("date_created") or "")[:10],
                    "status": o.get("status") or "",
                    "items": [(li.get("name") or "").strip() for li in (o.get("line_items") or []) if li.get("name")][:4],
                })
            processed += len(rows)
            if len(rows) < 50:
                break
            page += 1
        for g in groups.values():          # ساختِ پروفایل‌ها از داده‌های جمع‌شده
            e = _M.setdefault(_key("site", g["phone"]), {"channel": "site", "cid": g["phone"]})
            e["phone"] = g["phone"]
            if g["name"]:
                e["name"] = g["name"]
            e["orders"] = g["orders"]
            e["checked_at"] = _now()
            p = _person_for(e)
            if g["name"]:
                _add_name(p, g["name"])
            _link_phone(e, g["phone"])
            _apply_crm(p, g["orders"])
        _CRAWL.update({"last": _now(), "phones": len(groups), "orders": processed})
        _save()
        return {"ok": True, "phones": len(groups), "orders": processed}
    except Exception as e:  # noqa: BLE001
        _CRAWL["error"] = str(e)
        return {"ok": False, "error": str(e)}
    finally:
        _CRAWL["running"] = False


_load()
