"""DNA مشتری — بانکِ واحدِ هویت/رفتار/خرید در همهٔ کانال‌ها، روی SQLite (سریع و ایندکس‌شده).

هدف: مغز هر مخاطب را «بهتر از خودش» بشناسد و یک بانکِ واحد داشته باشیم که از هر کانالی (واتساپ/
اینستاگرام/تلگرام/سایت/CRM) هر دیتایی هست، کانتکتِ مستقل بسازد و به‌محضِ کشفِ کلیدِ مشترک (شماره یا
آیدیِ کانال) در یک پروفایل ادغام و در همه‌جا سینک شود.

ذخیره‌سازی: data/crm_index.db (SQLite، WAL) — برای مقیاسِ ده‌ها‌هزار کانتکت. اسکیمای پروفایل/هویت
همان دیکشنریِ قبلی است (به‌صورتِ blob JSON در ردیف) + ستون‌های ایندکس‌شده (pid/phone9) برای جستجوی آنی.
مهاجرت: اگر دیتابیس خالی بود و crm_index.json قدیمی موجود بود، یک‌بار وارد می‌شود.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DB = os.path.join(_HERE, "data", "crm_index.db")
_JSON = os.path.join(_HERE, "data", "crm_index.json")   # نسخهٔ قدیمی (فقط برای مهاجرت)
REFRESH_H = 12

_CONN = None
_CRAWL: dict = {"running": False, "last": 0, "phones": 0, "orders": 0, "leads": 0, "error": ""}

_PAID = ("completed", "processing", "on-hold", "deliver", "delivered")


# ---------------- کمک‌تابع‌های پایه ----------------
def _digits(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _ph9(s):
    d = _digits(s)
    return d[-9:] if len(d) >= 9 else ""


def _same_phone(a, b):
    a, b = _ph9(a), _ph9(b)
    return bool(a) and a == b


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


# ---------------- لایهٔ SQLite ----------------
def _c():
    global _CONN
    if _CONN is None:
        os.makedirs(os.path.dirname(_DB), exist_ok=True)
        _CONN = sqlite3.connect(_DB, check_same_thread=False)
        _CONN.execute("PRAGMA journal_mode=WAL")
        _CONN.execute("PRAGMA synchronous=NORMAL")
        _init_schema(_CONN)
        _migrate_json_if_needed(_CONN)
    return _CONN


def _init_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS persons(
            pid TEXT PRIMARY KEY, name TEXT, last_seen REAL, orders_count INTEGER DEFAULT 0, blob TEXT);
        CREATE TABLE IF NOT EXISTS identities(
            idkey TEXT PRIMARY KEY, pid TEXT, phone9 TEXT, blob TEXT);
        CREATE INDEX IF NOT EXISTS ix_ident_pid ON identities(pid);
        CREATE INDEX IF NOT EXISTS ix_ident_ph ON identities(phone9);
        CREATE INDEX IF NOT EXISTS ix_pers_seen ON persons(last_seen);
        CREATE INDEX IF NOT EXISTS ix_pers_oc ON persons(orders_count);
    """)
    conn.commit()


def _migrate_json_if_needed(conn):
    n = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    if n:
        return
    try:
        with open(_JSON, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:  # noqa: BLE001
        return
    persons = data.get("persons") or {}
    idents = data.get("identities") or {}
    if not persons and not idents:
        return
    for pid, p in persons.items():
        p.setdefault("pid", pid)
        _store_person(p, conn)
    for idkey, e in idents.items():
        _put_ident(e, conn)
    conn.commit()
    print(f"[dna] مهاجرت از JSON: {len(persons)} پروفایل، {len(idents)} هویت وارد شد")


def _load_person(pid, conn=None):
    if not pid:
        return None
    row = (conn or _c()).execute("SELECT blob FROM persons WHERE pid=?", (pid,)).fetchone()
    return json.loads(row[0]) if row else None


def _store_person(p, conn=None):
    conn = conn or _c()
    oc = int((p.get("crm") or {}).get("orders_count") or 0)
    conn.execute(
        "INSERT INTO persons(pid,name,last_seen,orders_count,blob) VALUES(?,?,?,?,?) "
        "ON CONFLICT(pid) DO UPDATE SET name=excluded.name,last_seen=excluded.last_seen,"
        "orders_count=excluded.orders_count,blob=excluded.blob",
        (p["pid"], p.get("name", ""), float(p.get("last_seen") or 0), oc, json.dumps(p, ensure_ascii=False)))


def _get_ident(channel, cid, conn=None):
    row = (conn or _c()).execute("SELECT pid, blob FROM identities WHERE idkey=?", (_key(channel, cid),)).fetchone()
    if not row:
        return None
    e = json.loads(row[1])
    e["pid"] = row[0] or e.get("pid")   # ستونِ pid معتبرتر است (بعد از ادغام، blob ممکن است کهنه باشد)
    return e


def _put_ident(e, conn=None):
    conn = conn or _c()
    conn.execute(
        "INSERT INTO identities(idkey,pid,phone9,blob) VALUES(?,?,?,?) "
        "ON CONFLICT(idkey) DO UPDATE SET pid=excluded.pid,phone9=excluded.phone9,blob=excluded.blob",
        (_key(e.get("channel"), e.get("cid")), e.get("pid", ""), _ph9(e.get("phone", "")),
         json.dumps(e, ensure_ascii=False)))


def _idents_by_phone(phone, conn=None):
    p9 = _ph9(phone)
    if not p9:
        return []
    rows = (conn or _c()).execute("SELECT pid, blob FROM identities WHERE phone9=?", (p9,)).fetchall()
    out = []
    for pid, blob in rows:
        e = json.loads(blob)
        e["pid"] = pid or e.get("pid")
        out.append(e)
    return out


# ---------------- ساختِ پروفایل و ادغام ----------------
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


def _add_name(p, name):
    name = (name or "").strip()
    if not name:
        return
    if name not in p["names"]:
        p["names"].append(name)
    if not p.get("name"):
        p["name"] = name


def _person_for(entry, conn):
    """پروفایلِ متناظرِ یک هویت را بده/بساز و هویت را به آن وصل کن (بدونِ ذخیرهٔ نهایی)."""
    pid = entry.get("pid")
    p = _load_person(pid, conn) if pid else None
    if not p:
        pid = "p:" + _key(entry.get("channel"), entry.get("cid"))
        p = _load_person(pid, conn) or _new_person(pid)
    entry["pid"] = pid
    _attach_identity(p, entry)
    return p


def _merge_persons(keep_pid, drop_pid, conn):
    if keep_pid == drop_pid:
        return keep_pid
    a, b = _load_person(keep_pid, conn), _load_person(drop_pid, conn)
    if not a or not b:
        return keep_pid
    for idkey, blob in conn.execute("SELECT idkey, blob FROM identities WHERE pid=?", (drop_pid,)).fetchall():
        ee = json.loads(blob)
        ee["pid"] = keep_pid   # هم ستون هم blob را به‌روز کن تا هویت به پروفایلِ درست وصل بماند
        conn.execute("UPDATE identities SET pid=?, blob=? WHERE idkey=?",
                     (keep_pid, json.dumps(ee, ensure_ascii=False), idkey))
    for i in b.get("identities", []):
        _attach_identity(a, i)
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
        a["crm"] = b["crm"]
    if b.get("notes") and not a.get("notes"):
        a["notes"] = b["notes"]
    conn.execute("DELETE FROM persons WHERE pid=?", (drop_pid,))
    _store_person(a, conn)
    return keep_pid


def _link_phone(entry, phone, conn):
    """شماره را روی پروفایل ثبت و همهٔ پروفایل‌های هم‌شماره را در یکی ادغام کن (سینکِ بین‌کانالی)."""
    ph = _digits(phone)
    p = _person_for(entry, conn)
    if ph and ph not in p["phones"]:
        p["phones"].append(ph)
    p9 = _ph9(ph)
    if not p9:
        return p
    other = {r[0] for r in conn.execute("SELECT DISTINCT pid FROM identities WHERE phone9=? AND pid<>?",
                                        (p9, p["pid"]))}
    for opid in other:
        keep, drop = sorted([p["pid"], opid])
        _merge_persons(keep, drop, conn)
        p = _load_person(keep, conn)
    entry["phone"] = ph
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
    return _get_ident(channel, cid) or {}


def link(channel, cid, phone=None, name=None, ig_id=None):
    """هویتِ یک مخاطب را ثبت/به‌روز کن (شماره، نام، آیدیِ اینستا) + پروفایلِ واحد + ادغامِ هم‌شماره."""
    conn = _c()
    e = _get_ident(channel, cid, conn) or {"channel": channel, "cid": str(cid or "")}
    if ig_id:
        e["ig_id"] = str(ig_id)
    p = _person_for(e, conn)
    if name:
        e["name"] = name
        _add_name(p, name)
    if phone:
        e["phone"] = _digits(phone)
        p = _link_phone(e, phone, conn)
    _put_ident(e, conn)
    _store_person(p, conn)
    conn.commit()
    return e


def touch(channel, cid, name=None):
    """هویتِ مخاطب را ثبت کن (بدونِ زدن به سایت) — «افزودن به کانتکت‌ها» + ساختِ پروفایل."""
    if not cid:
        return
    conn = _c()
    e = _get_ident(channel, cid, conn) or {"channel": channel, "cid": str(cid)}
    p = _person_for(e, conn)
    if name and e.get("name") != name:
        e["name"] = name
        _add_name(p, name)
    _put_ident(e, conn)
    _store_person(p, conn)
    conn.commit()


def observe(channel, cid, name=None, signals=None):
    """رفتارِ یک گفتگو را روی پروفایلِ DNA تجمیع کن (بعد از هر پاسخِ مغز)."""
    if not cid:
        return
    try:
        conn = _c()
        e = _get_ident(channel, cid, conn) or {"channel": channel, "cid": str(cid)}
        if name and not e.get("name"):
            e["name"] = name
        p = _person_for(e, conn)
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
            p["products"][pr] = p["products"].get(pr, 0) + 2
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
        _put_ident(e, conn)
        _store_person(p, conn)
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def by_phone(phone):
    return _idents_by_phone(phone)


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
    conn = _c()
    e = _get_ident(channel, cid, conn) or {"channel": channel, "cid": str(cid or "")}
    e["phone"] = _digits(phone)
    p = _link_phone(e, phone, conn)
    now = _now()
    if "orders" not in e or (now - float(e.get("checked_at", 0))) > REFRESH_H * 3600:
        fresh = None
        for o in _idents_by_phone(phone, conn):
            if _key(o.get("channel"), o.get("cid")) != _key(channel, cid) \
                    and "orders" in o and (now - float(o.get("checked_at", 0))) < REFRESH_H * 3600:
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
        for o in _idents_by_phone(phone, conn):
            if _key(o.get("channel"), o.get("cid")) != _key(channel, cid):
                o["orders"] = e.get("orders", [])
                o["checked_at"] = e.get("checked_at", now)
                _put_ident(o, conn)
        _apply_crm(p, e.get("orders") or [])
        _store_person(p, conn)
    _put_ident(e, conn)
    conn.commit()
    return _fmt_history(e)


def dna_hint(channel, cid):
    """پروفایلِ رفتاریِ مشتری برای تزریق به مغز (مستقل از شماره؛ کاربرانِ فقط-اینستاگرامی هم DNA دارند)."""
    e = _get_ident(channel, cid)
    if not e:
        return ""
    p = _load_person(e.get("pid"))
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
        bits.append("به قیمت حساس بوده — ارزش و گارانتی را آرام یادآوری کن")
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
    chans = list((p.get("channels") or {}).keys())
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
    e = _get_ident(channel, cid)
    if not e:
        return {}
    p = _load_person(e.get("pid"))
    return _public_person(p) if p else {}


def get_profile(pid):
    p = _load_person(pid)
    return _public_person(p) if p else {}


def top_profiles(limit=60):
    rows = _c().execute("SELECT blob FROM persons ORDER BY last_seen DESC LIMIT ?", (max(1, limit),)).fetchall()
    return [_public_person(json.loads(r[0]), brief=True) for r in rows]


def stats():
    conn = _c()
    persons = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    buyers = conn.execute("SELECT COUNT(*) FROM persons WHERE orders_count>0").fetchone()[0]
    withphone = conn.execute("SELECT COUNT(DISTINCT pid) FROM identities WHERE phone9<>''").fetchone()[0]
    multichannel = conn.execute(
        "SELECT COUNT(*) FROM (SELECT pid FROM identities GROUP BY pid HAVING COUNT(DISTINCT "
        "substr(idkey,1,instr(idkey,':')-1))>1)").fetchone()[0]
    idents = conn.execute("SELECT COUNT(*) FROM identities").fetchone()[0]
    return {"persons": persons, "with_phone": withphone, "buyers": buyers, "multichannel": multichannel,
            "identities": idents,
            "crawl": {"running": _CRAWL["running"], "last": _CRAWL["last"], "phones": _CRAWL["phones"],
                      "orders": _CRAWL["orders"], "leads": _CRAWL.get("leads", 0), "error": _CRAWL["error"]}}


# ---------------- خزندهٔ backfill (سفارش‌ها + لیدهای ثبت‌نامی) ----------------
async def crawl_from_woo(max_orders=1500):
    """از سفارش‌ها و مشتریانِ ثبت‌نامیِ ووکامرس، پروفایلِ واحدِ DNA بساز/به‌روز کن (ادغام با شماره)."""
    if _CRAWL["running"]:
        return {"ok": False, "error": "already running"}
    _CRAWL["running"] = True
    _CRAWL["error"] = ""
    try:
        import woo
        conn = _c()
        groups = {}
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
            _CRAWL["orders"] = processed
            _CRAWL["phones"] = len(groups)
            if len(rows) < 50:
                break
            page += 1
        for g in groups.values():
            e = _get_ident("site", g["phone"], conn) or {"channel": "site", "cid": g["phone"]}
            e["phone"] = g["phone"]
            if g["name"]:
                e["name"] = g["name"]
            e["orders"] = g["orders"]
            e["checked_at"] = _now()
            p = _link_phone(e, g["phone"], conn)
            if g["name"]:
                _add_name(p, g["name"])
            _apply_crm(p, g["orders"])
            _put_ident(e, conn)
            _store_person(p, conn)
        conn.commit()
        # پاسِ دوم: مشتریانِ ثبت‌نامیِ سایت (لیدهای بدونِ خرید هم پروفایل بگیرند)
        leads = 0
        cpage = 1
        while cpage <= 40:
            try:
                crows = await woo.get("customers", {"per_page": 50, "page": cpage,
                                                    "orderby": "registered_date", "order": "desc"})
            except Exception:  # noqa: BLE001
                break
            if not isinstance(crows, list) or not crows:
                break
            for c in crows:
                b = c.get("billing") or {}
                ph = _digits(b.get("phone") or "")
                if len(ph) < 9:
                    continue
                nm = ((c.get("first_name") or b.get("first_name") or "") + " "
                      + (c.get("last_name") or b.get("last_name") or "")).strip()
                e = _get_ident("site", ph, conn) or {"channel": "site", "cid": ph}
                e["phone"] = ph
                if nm and not e.get("name"):
                    e["name"] = nm
                p = _link_phone(e, ph, conn)
                if nm:
                    _add_name(p, nm)
                if not c.get("is_paying_customer") and "lead" not in p.get("interests", []) \
                        and not (p.get("crm") or {}).get("orders_count"):
                    p.setdefault("interests", []).append("lead")
                _put_ident(e, conn)
                _store_person(p, conn)
                leads += 1
            conn.commit()
            if len(crows) < 50:
                break
            cpage += 1
        _CRAWL.update({"last": _now(), "phones": len(groups), "orders": processed, "leads": leads})
        return {"ok": True, "phones": len(groups), "orders": processed, "leads": leads}
    except Exception as e:  # noqa: BLE001
        _CRAWL["error"] = str(e)
        return {"ok": False, "error": str(e)}
    finally:
        _CRAWL["running"] = False
