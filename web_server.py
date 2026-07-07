"""بک‌اند چت سایت: FastAPI با اندپوینت /chat و ویجت قابل‌جاسازی.

داخل همان حلقه‌ی asyncioِ تلگرام اجرا می‌شود (serve یک کوروتین است).
"""
from __future__ import annotations

import asyncio
import os
from collections import deque
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

import time

import assistant
import botusers
import config
import llm
import modelcfg
import persona

_models_cache = {"at": 0.0, "list": []}


async def _available_models():
    """فهرستِ مدل‌های چت/عکسِ حسابِ OpenAI (زنده، با کشِ ۵ دقیقه‌ای)."""
    if time.time() - _models_cache["at"] < 300 and _models_cache["list"]:
        return _models_cache["list"]
    try:
        resp = await llm.client().models.list()
        bad = ("embedding", "tts", "whisper", "audio", "realtime", "image", "dall-e", "moderation", "transcribe", "search", "codex", "computer")
        ids = sorted({m.id for m in resp.data if m.id.startswith(("gpt-", "o1", "o3", "o4")) and not any(b in m.id for b in bad)})
        _models_cache["at"] = time.time()
        _models_cache["list"] = ids
        return ids
    except Exception as e:  # noqa: BLE001
        print(f"[brain] fetch models failed: {type(e).__name__}: {e}")
        return _models_cache["list"]

CHANNEL = "web"
_tg_app = None  # برای ارجاع به ادمین از طریق تلگرام (هنگام serve ست می‌شود)
_site_chats = deque(maxlen=80)  # آخرین گفتگوهای چتِ سایت برای پایش در داشبورد (ماندگار روی دیسک)
_SITE_CHATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "site_chats.json")
try:
    with open(_SITE_CHATS_FILE, encoding="utf-8") as _f:
        _site_chats.extend(__import__("json").load(_f) or [])
except Exception:  # noqa: BLE001
    pass


def _site_chats_save():
    try:
        import json as _json
        tmp = _SITE_CHATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(list(_site_chats), f, ensure_ascii=False)
        os.replace(tmp, _SITE_CHATS_FILE)
    except Exception:  # noqa: BLE001
        pass
_bcast = {"running": False, "sent": 0, "failed": 0, "total": 0, "stop": False}  # وضعیتِ ارسالِ گروهیِ تلگرام

app = FastAPI(title="Javaherian Sales Assistant")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.WEB_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-SB-Token"],
)


class ChatIn(BaseModel):
    session_id: str
    message: str
    phone: str = ""   # شمارهٔ تماسِ کاربرِ سایت (اگر موجود) — تا دوباره پرسیده نشود
    name: str = ""


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/stats")
async def stats():
    """شمار کاربرانِ ربات (برای داشبورد پیام‌رسانی)."""
    return botusers.counts()


@app.post("/chat")
async def chat(body: ChatIn):
    sid = (body.session_id or "anon").strip()[:64]
    answer, ctx = await assistant.reply(
        CHANNEL, sid, body.message, user_name=(body.name or None), customer_phone=(body.phone or None))
    cards = ctx.get("cards") or []
    if cards:   # ویجتِ سایت فقط متن نشان می‌دهد → کارت‌ها را مثلِ واتساپ/اینستاگرام به‌صورتِ متن ضمیمه کن
        try:
            answer = (answer + "\n\n" + assistant._cards_as_text(cards)).strip()
        except Exception:  # noqa: BLE001
            pass
    if ctx.get("handoff"):
        await _notify_admins(sid, body.message, ctx["handoff"])
    try:  # ثبت برای پایشِ چتِ سایت در داشبورد
        import clock
        _site_chats.appendleft({"t": clock.tehran_now().strftime("%m-%d %H:%M"), "sid": sid,
                                "name": (body.name or "").strip(), "q": (body.message or "")[:200], "a": (answer or "")[:400]})
        _site_chats_save()
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse({"reply": answer})


async def _notify_admins(sid, last_text, handoff):
    if not (_tg_app and config.ADMIN_USER_IDS):
        return
    note = (
        "🔔 درخواست اپراتور (چت سایت)\n"
        f"نشست: {sid}\n"
        f"دلیل: {handoff.get('reason', '')}\n"
        f"تماس: {handoff.get('contact') or '—'}\n"
        f"آخرین پیام: {last_text}"
    )
    for admin_id in config.ADMIN_USER_IDS:
        try:
            await _tg_app.bot.send_message(chat_id=admin_id, text=note)
        except Exception as e:  # noqa: BLE001
            print(f"[web] ارسال هشدار به ادمین {admin_id} ناموفق: {e}")


# ---------- اتصال CRM (نقش sale-brain-v2) ----------
# افزونه‌ی CRM این‌ها را صدا می‌زند: GET /api/client/me و POST /api/chat با هدر X-SB-Token.
class BrainChatIn(BaseModel):
    messages: list = []
    user_prompt: str = ""
    catalog: Any = None
    temperature: float | None = None
    max_tokens: int | None = None
    cards_as_text: bool = True  # کانال‌هایی که خودشان کارت رندر می‌کنند → False (متنِ تمیز + cards ساختاریافته)
    reply_context: dict | list | None = None  # کارت(های) ریپلای‌شده {"url"/"name"/"reference"} — یکی یا لیستی از چند محصول
    customer: dict | None = None  # {channel, id, name} — برای ردگیریِ کارت‌های نشان‌داده‌شده (عدمِ‌تکرار/صفحه‌بندی)


def _check_sb_token(token):
    if not config.SALE_BRAIN_TOKEN:
        raise HTTPException(status_code=503, detail="sale-brain token not configured")
    if token != config.SALE_BRAIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")


class RecoveryNotifyIn(BaseModel):
    text: str = ""                       # کارتِ خوانا برای همکاران (HTML)
    wa_link: str = ""                    # لینکِ آمادهٔ web.whatsapp.com/send با پیامِ پرشده
    button_text: str = "📲 ارسال در واتساپ‌وب"


@app.post("/api/recovery-notify")
async def recovery_notify(body: RecoveryNotifyIn, x_sb_token: str = Header(None, alias="X-SB-Token")):
    """کارتِ بازیابیِ سبدِ رها را با دکمهٔ آمادهٔ web.whatsapp.com در گروهِ «پیگیری مشتریان و CRM» پست می‌کند."""
    _check_sb_token(x_sb_token)
    if not _tg_app:
        return {"ok": False, "error": "telegram not ready"}
    gid = config.ORDERS_GROUP_ID or config.SUPPORT_GROUP_ID or config.STAFF_GROUP_ID
    if not gid:
        return {"ok": False, "error": "no group configured"}
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(body.button_text, url=body.wa_link)]]) if body.wa_link else None
    try:
        sent = await _tg_app.bot.send_message(gid, body.text or "🛒 بازیابیِ سبدِ رها", reply_markup=kb,
                                              parse_mode="HTML", disable_web_page_preview=True)
        return {"ok": True, "message_id": sent.message_id}
    except Exception as e:  # noqa: BLE001
        print(f"[brain] پستِ بازیابی به گروه ناموفق: {type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/brain/performance")
async def brain_performance(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """عملکردِ روزانهٔ ربات فروش: پاسخ‌ها به‌تفکیکِ کانال + سفارش/رسید/ارجاع/مدیا + کاربران."""
    _check_sb_token(x_sb_token)
    import metrics
    snap = metrics.snapshot()
    try:
        snap["users"] = botusers.counts()
    except Exception:  # noqa: BLE001
        snap["users"] = {}
    snap["ok"] = True
    return snap


@app.get("/api/brain/analytics")
async def brain_analytics(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """تحلیلِ رفتار و خواستهٔ مشتری: محصول/برندِ پرتقاضا، سیگنالِ خرید، اعتراض/ریزش، حال‌وهوا، مشتریانِ داغ."""
    _check_sb_token(x_sb_token)
    import analytics
    snap = analytics.snapshot()
    snap["ok"] = True
    return snap


@app.get("/api/brain/dna/list")
async def brain_dna_list(limit: int = 60, x_sb_token: str = Header(None, alias="X-SB-Token")):
    """فهرستِ پروفایل‌های واحدِ مشتری (DNA) — کانتکتِ ادغام‌شدهٔ همهٔ کانال‌ها + خلاصهٔ رفتار/خرید."""
    _check_sb_token(x_sb_token)
    import crm_index
    return {"ok": True, "stats": crm_index.stats(), "profiles": crm_index.top_profiles(limit=max(1, min(limit, 200)))}


@app.get("/api/brain/dna/get")
async def brain_dna_get(pid: str = "", channel: str = "", cid: str = "",
                        x_sb_token: str = Header(None, alias="X-SB-Token")):
    """پروفایلِ کاملِ یک مشتری (با pid یا با channel+cid)."""
    _check_sb_token(x_sb_token)
    import crm_index
    prof = crm_index.get_profile(pid) if pid else crm_index.profile(channel, cid)
    return {"ok": bool(prof), "profile": prof or {}}


@app.post("/api/brain/dna/crawl")
async def brain_dna_crawl(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """خزندهٔ DNA را دستی اجرا کن: از سفارش‌های موجودِ ووکامرس، پروفایل‌های واحدِ مشتری بساز/به‌روز کن."""
    _check_sb_token(x_sb_token)
    import crm_index
    if crm_index._CRAWL.get("running"):
        return {"ok": False, "error": "already running", "crawl": crm_index.stats().get("crawl")}
    asyncio.create_task(crm_index.crawl_from_woo())
    return {"ok": True, "started": True}


@app.post("/api/brain/handoff/reset-all")
async def brain_handoff_reset(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """همهٔ اتصال‌های زندهٔ فعال (هندآف) را ببند و گفتگوها را به دستیارِ هوشمند برگردان."""
    _check_sb_token(x_sb_token)
    if not _tg_app:
        return {"ok": False, "error": "telegram app not ready"}
    import telegram_bot as _tb
    done = await _tb.reset_all_handoffs(_tg_app.bot)
    return {"ok": True, "closed": done, "count": len(done)}


@app.get("/api/brain/handoff/status")
async def brain_handoff_status(x_sb_token: str = Header(None, alias="X-SB-Token")):
    _check_sb_token(x_sb_token)
    import telegram_bot as _tb
    return {"ok": True, "active": [{"channel": k[0], "cid": k[1], "anchor": v}
                                   for k, v in _tb._handoff_active.items()],
            "mapped_msgs": len(_tb._handoffs), "escalations": len(_tb._escalations)}


@app.post("/api/brain/dna/import-userbot")
async def brain_dna_import_userbot(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """بانکِ کانتکتِ یوزربات (CRMِ سایت + واتساپ + چت‌ها) را به بانکِ واحدِ DNA وارد/ادغام کن."""
    _check_sb_token(x_sb_token)
    import crm_index
    if crm_index._IMPORT.get("running"):
        return {"ok": False, "error": "already running", "import": dict(crm_index._IMPORT)}
    asyncio.create_task(asyncio.to_thread(crm_index.import_userbot))   # ترد جدا با اتصالِ اختصاصی
    return {"ok": True, "started": True}


@app.get("/api/brain/dna/import-status")
async def brain_dna_import_status(x_sb_token: str = Header(None, alias="X-SB-Token")):
    _check_sb_token(x_sb_token)
    import crm_index
    return {"ok": True, "import": dict(crm_index._IMPORT), "stats": crm_index.stats()}


class MgrChatIn(BaseModel):
    question: str = ""
    model: str | None = None
    history: list = []
    image_b64: str = ""     # فایل/تصویرِ اختیاری برای تحلیل
    mime: str = "image/jpeg"


@app.get("/api/brain/sales-report")
async def brain_sales_report(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """آخرین گزارشِ مدیریتیِ فروش (روزی یک‌بار خودکار ساخته می‌شود) + تاریخچهٔ کوتاه."""
    _check_sb_token(x_sb_token)
    import sales_ai
    rec = sales_ai.latest()
    if not isinstance(rec, dict):
        rec = {"ok": False}
    rec["history"] = sales_ai.history()[:12]
    return rec


@app.post("/api/brain/sales-report/run")
async def brain_sales_report_run(body: MgrChatIn | None = None, x_sb_token: str = Header(None, alias="X-SB-Token")):
    """ساختِ فوریِ گزارش — دکمهٔ «تحلیلِ الان» در داشبورد (با مدلِ انتخابی یا پیش‌فرض gpt-5.5)."""
    _check_sb_token(x_sb_token)
    import sales_ai
    mdl = (body.model if body else None) or None
    return await sales_ai.run_analysis(model=mdl)


@app.post("/api/brain/mgr-chat")
async def brain_mgr_chat(body: MgrChatIn, x_sb_token: str = Header(None, alias="X-SB-Token")):
    """چتِ مدیریتی: پاسخِ دقیق به سوالِ مدیر «طبقِ آمار و گزارش»."""
    _check_sb_token(x_sb_token)
    import sales_ai
    ans = await sales_ai.ask(body.question, model=body.model, history_msgs=body.history,
                             image_b64=(body.image_b64 or None), mime=body.mime)
    return {"ok": True, "answer": ans}


class TgBroadcastIn(BaseModel):
    text: str = ""
    min_delay: float = 0.1   # رباتِ رسمیِ تلگرام ~۳۰ پیام/ثانیه مجاز است؛ ارسال سریع، شروع/توقف دستی
    max_delay: float = 0.2
    file_b64: str = ""     # فایلِ اختیاری (عکس/سند) — base64
    file_name: str = ""
    mime: str = ""


async def _run_broadcast(text, dmin, dmax, file_bytes=None, file_name="", is_image=False):
    """ارسالِ گروهیِ ضدبلاک به همهٔ کاربرانِ رباتِ فروش (متن و/یا فایل). file_id بعد از بارِ اول بازاستفاده می‌شود."""
    import random
    ids = botusers.all_ids()
    _bcast.update({"running": True, "sent": 0, "failed": 0, "total": len(ids), "stop": False})
    file_id = None
    cap = text or None
    try:
        for uid in ids:
            if _bcast["stop"] or not _tg_app:
                break
            try:
                if file_bytes is not None:
                    src = file_id if file_id else bytes(file_bytes)
                    if is_image:
                        m = await _tg_app.bot.send_photo(int(uid), photo=src, caption=cap)
                        if file_id is None and m and m.photo:
                            file_id = m.photo[-1].file_id
                    else:
                        m = await _tg_app.bot.send_document(int(uid), document=src, filename=file_name or "file", caption=cap)
                        if file_id is None and m and getattr(m, "document", None):
                            file_id = m.document.file_id
                else:
                    await _tg_app.bot.send_message(int(uid), text, disable_web_page_preview=True)
                _bcast["sent"] += 1
            except Exception:  # noqa: BLE001
                _bcast["failed"] += 1
            await asyncio.sleep(random.uniform(dmin, dmax))
    finally:
        _bcast["running"] = False


@app.get("/api/brain/tg/overview")
async def brain_tg_overview(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """تبِ رباتِ فروشِ تلگرام: گفتگوها + کاربران + پاسخ‌ها + وضعیتِ ارسالِ گروهی."""
    _check_sb_token(x_sb_token)
    import tgstore
    snap = tgstore.snapshot()
    snap["ok"] = True
    snap["users_total"] = botusers.counts()
    try:
        import metrics
        m = metrics.snapshot()
        snap["replies_today"] = (m.get("today") or {}).get("reply:telegram", 0)
        snap["replies_total"] = (m.get("totals") or {}).get("reply:telegram", 0)
    except Exception:  # noqa: BLE001
        pass
    snap["broadcast"] = {k: _bcast.get(k) for k in ("running", "sent", "failed", "total")}
    return snap


@app.post("/api/brain/tg/broadcast")
async def brain_tg_broadcast(body: TgBroadcastIn, x_sb_token: str = Header(None, alias="X-SB-Token")):
    """ارسالِ پیام به همهٔ اعضای رباتِ فروش (ضدبلاک، در پس‌زمینه)."""
    _check_sb_token(x_sb_token)
    if not _tg_app:
        return {"ok": False, "error": "تلگرام متصل نیست"}
    if _bcast["running"]:
        return {"ok": False, "error": "یک ارسالِ گروهی در حالِ اجراست"}
    text = (body.text or "").strip()
    file_bytes = None
    is_image = False
    if body.file_b64:
        import base64
        try:
            file_bytes = base64.b64decode(body.file_b64)
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "فایل نامعتبر است"}
        is_image = (body.mime or "").startswith("image/")
    if not text and file_bytes is None:
        return {"ok": False, "error": "متن یا فایل بده"}
    dmin = max(0.05, float(body.min_delay or 0.1))   # فقط رعایتِ سقفِ مجازِ تلگرام (~۳۰/ثانیه)
    dmax = max(dmin, float(body.max_delay or 0.2))
    asyncio.create_task(_run_broadcast(text, dmin, dmax, file_bytes, body.file_name, is_image))
    return {"ok": True, "total": len(botusers.all_ids())}


@app.post("/api/brain/tg/broadcast/stop")
async def brain_tg_broadcast_stop(x_sb_token: str = Header(None, alias="X-SB-Token")):
    _check_sb_token(x_sb_token)
    _bcast["stop"] = True
    return {"ok": True}


@app.get("/api/brain/funnel")
async def brain_funnel(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """قیفِ فروش به‌تفکیکِ کانال: پاسخ → مشتریِ داغ → سفارش → رسیدِ پرداخت + نرخِ تبدیل + فروشِ واقعیِ سایت."""
    _check_sb_token(x_sb_token)
    import analytics
    import metrics
    T = metrics.snapshot().get("totals", {})
    an = analytics.snapshot()
    leads_by_ch: dict = {}
    for l in (an.get("hot_leads") or []):
        c = l.get("ch", "")
        leads_by_ch[c] = leads_by_ch.get(c, 0) + 1
    rows = []
    for ch in ("whatsapp", "instagram", "telegram", "web"):
        replies = T.get(f"reply:{ch}", 0)
        orders = T.get(f"order:{ch}", 0)
        rows.append({"channel": ch, "replies": replies, "hot": leads_by_ch.get(ch, 0),
                     "orders": orders, "receipts": T.get(f"receipt:{ch}", 0),
                     "conversion": round(orders / replies * 100, 1) if replies else 0.0})
    out = {"ok": True, "channels": rows,
           "total": {"replies": T.get("reply", 0), "orders": T.get("order", 0), "receipts": T.get("receipt", 0)}}
    import salescfg
    out["ab"] = {"enabled": bool(salescfg.get("ab_enabled", False))}
    for k in ("A", "B", "C"):
        out["ab"][k] = {"replies": T.get(f"ab:{k}", 0), "orders": T.get(f"ab_order:{k}", 0)}
    try:
        import sales_ai
        out["woo"] = await sales_ai._woo_sales()
    except Exception:  # noqa: BLE001
        out["woo"] = {}
    return out


@app.get("/api/brain/products/status")
async def brain_products_status(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """وضعیتِ ایندکسِ محلیِ محصولات (تعداد، سنِ داده، بازهٔ به‌روزرسانی)."""
    _check_sb_token(x_sb_token)
    import productindex
    return {"ok": True, **productindex.status()}


@app.post("/api/brain/products/sync")
async def brain_products_sync(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """به‌روزرسانیِ فوریِ ایندکسِ محصولات (دکمهٔ «به‌روزرسانیِ الان»)."""
    _check_sb_token(x_sb_token)
    import productindex
    if productindex.status()["syncing"]:
        return {"ok": False, "error": "همگام‌سازی هم‌اکنون در حالِ اجراست"}
    asyncio.create_task(productindex.sync(force=True))
    return {"ok": True, "started": True}


@app.get("/api/client/me")
async def brain_me(x_sb_token: str = Header(None, alias="X-SB-Token")):
    _check_sb_token(x_sb_token)
    # سهمیه‌ی نامحدود (خودمیزبان)؛ CRM فقط نمایش می‌دهد
    return {"ok": True, "client": {"name": "javaherian-sale-brain", "quota_used": 0, "quota_limit": 0}}


def _last_user_content(messages):
    for m in reversed(messages or []):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        if role == "user":
            return (m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or ""
    return ""


@app.post("/api/chat")
async def brain_chat(body: BrainChatIn, x_sb_token: str = Header(None, alias="X-SB-Token")):
    _check_sb_token(x_sb_token)
    cust = body.customer or {}
    _ch, _cid = (cust.get("channel") or ""), str(cust.get("id") or "")
    # حین اتصالِ زندهٔ فعال به اپراتور: پیامِ کاربر را به گروه رله کن و AI را اصلاً صدا نزن
    if _ch and _cid and _tg_app:
        import telegram_bot as _tb
        if _tb.is_handoff_active(_ch, _cid):
            try:
                await _tb.relay_user_to_group(_tg_app.bot, _ch, _cid, _last_user_content(body.messages), cust.get("name", ""))
            except Exception as e:  # noqa: BLE001
                print(f"[brain] رله‌ی پیامِ کاربر به گروه ناموفق: {type(e).__name__}: {e}")
            return {"text": "", "cards": [], "handoff": True, "handoff_active": True, "quota_used": 0, "quota_limit": 0}
    import time as _t
    _start = _t.monotonic()
    print(f"[brain] /api/chat دریافت شد ({len(body.messages or [])} پیام)")
    text, ctx = await assistant.answer_messages(
        body.messages, body.user_prompt, render_cards_inline=body.cards_as_text,
        reply_context=body.reply_context, customer=body.customer)
    print(f"[brain] پاسخ آماده در {_t.monotonic() - _start:.1f} ثانیه (طول متن={len(text)})")
    handoff = ctx.get("handoff")
    # مغز تصمیم به ارجاعِ زنده گرفت → اتصالِ دوطرفه را شروع کن و پیامِ «به همکار وصل شدید» بده
    if handoff and _ch and _cid and _tg_app:
        import telegram_bot as _tb
        try:
            if await _tb.start_handoff(_tg_app.bot, _ch, _cid, cust.get("name", ""),
                                       _last_user_content(body.messages), (handoff or {}).get("reason", "")):
                text = ("شما رو به همکارِ انسانیمون وصل کردم 👤🌟 چند لحظه صبر کنید؛ همین‌جا پاسخگوتون هستن. "
                        "هر وقت خواستید دوباره با دستیارِ هوشمند ادامه بدید، کافیه همین‌جا بگید.")
        except Exception as e:  # noqa: BLE001
            print(f"[brain] شروعِ اتصالِ زنده ناموفق: {type(e).__name__}: {e}")
    if _ch not in ("whatsapp", "instagram"):  # چتِ سایت (وب) → ثبت برای پایشِ داشبورد (کانال‌های پیام‌رسان جدا)
        try:
            import clock
            _site_chats.appendleft({"t": clock.tehran_now().strftime("%m-%d %H:%M"), "sid": _cid or "web",
                                    "name": (cust.get("name") or "").strip(),
                                    "q": (_last_user_content(body.messages) or "")[:200], "a": (text or "")[:400]})
            _site_chats_save()
        except Exception:  # noqa: BLE001
            pass
    # درخواستِ مدیای مچ از کانال‌های غیرتلگرام → اعلان در گروهِ عکس‌وویدئو (وگرنه همکاران هیچ‌وقت باخبر نمی‌شوند)
    _wmr = ctx.get("wrist_media_request")
    if _wmr and _ch and _ch != "telegram" and _cid and _tg_app:
        try:
            import telegram_bot as _tb2
            await _tb2.post_wrist_request(_tg_app.bot, _wmr, _ch, _cid)
        except Exception as e:  # noqa: BLE001
            print(f"[brain] اعلانِ درخواستِ مچ به گروه ناموفق: {type(e).__name__}: {e}")
    return {
        "text": text,
        # دادهٔ ساختاریافته برای کانال‌هایی که خودшان رندر می‌کنند (کارت/مدیا/سفارش/ارجاع):
        "cards": ctx.get("cards") or [],
        "wrist_media": ctx.get("wrist_media") or None,
        "wrist_media_request": ctx.get("wrist_media_request") or None,
        "wrist_media_company_stock": ctx.get("wrist_media_company_stock") or None,
        "order": ctx.get("order") or None,
        "handoff": bool(handoff),
        "handoff_reason": (handoff or {}).get("reason", "") if handoff else "",
        "name_update": ctx.get("name_update") or None,
        "quota_used": 0,
        "quota_limit": 0,
    }


class FollowupIn(BaseModel):
    channel: str = ""
    name: str = ""
    messages: list = []      # [{role:'user'|'assistant', content:'...'}] — گفتگوی اخیرِ همان کانال


@app.post("/api/followup")
async def brain_followup(body: FollowupIn, x_sb_token: str = Header(None, alias="X-SB-Token")):
    """پیامِ پیگیریِ هوشمند (نه ثابت) برای یک گفتگوی ساکت می‌سازد؛ کانال‌ها (wa/ig/tg) صدا می‌زنند."""
    _check_sb_token(x_sb_token)
    text = await assistant.generate_followup(body.messages, body.name, body.channel)
    return {"ok": bool(text), "text": text}


@app.get("/api/sitechat/recent")
async def sitechat_recent(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """آخرین گفتگوهای چتِ سایت برای پایش در کاکپیت + شمارِ امروز (برای خلاصه/نمودار)."""
    _check_sb_token(x_sb_token)
    today = ""
    try:
        import clock
        today = clock.tehran_now().strftime("%m-%d")
    except Exception:  # noqa: BLE001
        pass
    today_count = sum(1 for x in _site_chats if today and (x.get("t") or "").startswith(today))
    return {"ok": True, "items": list(_site_chats)[:40], "today": today_count, "buffered": len(_site_chats)}


class BrainSettingsIn(BaseModel):
    store_info: str | None = None
    persona_extra: str | None = None
    chat_model: str | None = None       # مدلِ پاسخگویی (متن)
    vision_model: str | None = None     # مدلِ عکس
    analysis_model: str | None = None   # مدلِ اختصاصیِ تحلیلِ فروش/مدیریتی
    first_buyer_coupon: str | None = None  # کدِ تخفیفِ خریدِ اول (مغز در پیگیری استفاده می‌کند)
    ab_enabled: bool | None = None         # آزمونِ شخصیت/لحنِ مغز (A/B/C)
    ab_variant_a: str | None = None        # شخصیتِ A
    ab_variant_b: str | None = None        # شخصیتِ B
    ab_variant_c: str | None = None        # شخصیتِ C
    product_sync_hours: float | None = None  # بازهٔ به‌روزرسانیِ ایندکسِ محلیِ محصولات (ساعت)


@app.get("/api/brain/settings")
async def brain_settings_get(x_sb_token: str = Header(None, alias="X-SB-Token")):
    """پرسونا/اطلاعاتِ فروشگاه + مدلِ چت و عکس + فهرستِ مدل‌های در دسترس — برای تبِ «مغز» در داشبورد."""
    _check_sb_token(x_sb_token)
    return {"ok": True, "store_info": persona.load_store_info(), "persona_extra": persona.load_persona_extra(),
            "chat_model": modelcfg.chat_model(), "vision_model": modelcfg.vision_model(),
            "analysis_model": modelcfg.analysis_model(),
            "first_buyer_coupon": __import__("salescfg").get("first_buyer_coupon", ""),
            "ab_enabled": bool(__import__("salescfg").get("ab_enabled", False)),
            "ab_variant_a": __import__("salescfg").get("ab_variant_a", ""),
            "ab_variant_b": __import__("salescfg").get("ab_variant_b", ""),
            "ab_variant_c": __import__("salescfg").get("ab_variant_c", ""),
            "product_sync_hours": float(__import__("salescfg").get("product_sync_hours", 8) or 8),
            "available_models": await _available_models(),
            "reasoning": getattr(config, "OPENAI_REASONING_EFFORT", ""),
            "max_tokens": getattr(config, "OPENAI_MAX_COMPLETION_TOKENS", "")}


@app.post("/api/brain/settings")
async def brain_settings_set(body: BrainSettingsIn, x_sb_token: str = Header(None, alias="X-SB-Token")):
    """ذخیرهٔ دستیِ اطلاعاتِ فروشگاه + دستورهای اضافیِ پرسونا از داشبورد (بلافاصله در پرامپتِ بعدی اعمال می‌شود)."""
    _check_sb_token(x_sb_token)
    saved = []
    if body.store_info is not None:
        with open(persona._STORE_INFO_PATH, "w", encoding="utf-8") as f:
            f.write(body.store_info)
        saved.append("store_info")
    if body.persona_extra is not None:
        os.makedirs(os.path.dirname(persona._PERSONA_EXTRA_PATH), exist_ok=True)
        with open(persona._PERSONA_EXTRA_PATH, "w", encoding="utf-8") as f:
            f.write(body.persona_extra)
        saved.append("persona_extra")
    if body.chat_model or body.vision_model or body.analysis_model:
        modelcfg.set_models(chat=body.chat_model, vision=body.vision_model, analysis=body.analysis_model)  # بدونِ ری‌استارت
        saved.append("models")
    if body.first_buyer_coupon is not None:
        import salescfg
        salescfg.set_many(first_buyer_coupon=body.first_buyer_coupon.strip())
        saved.append("first_buyer_coupon")
    if any(v is not None for v in (body.ab_enabled, body.ab_variant_a, body.ab_variant_b, body.ab_variant_c)):
        import salescfg
        kw = {}
        if body.ab_enabled is not None:
            kw["ab_enabled"] = bool(body.ab_enabled)
        if body.ab_variant_a is not None:
            kw["ab_variant_a"] = body.ab_variant_a.strip()
        if body.ab_variant_b is not None:
            kw["ab_variant_b"] = body.ab_variant_b.strip()
        if body.ab_variant_c is not None:
            kw["ab_variant_c"] = body.ab_variant_c.strip()
        salescfg.set_many(**kw)
        saved.append("ab_test")
    if body.product_sync_hours is not None:
        import salescfg
        salescfg.set_many(product_sync_hours=max(0.25, min(float(body.product_sync_hours), 168)))
        saved.append("product_sync_hours")
    return {"ok": True, "saved": saved}


class VisionIn(BaseModel):
    image_b64: str = ""      # بایتِ تصویر به base64 (کانال‌ها این را می‌فرستند)
    image_url: str = ""      # یا یک data-url/http-url مستقیم
    mime: str = "image/jpeg"
    caption: str = ""
    messages: list = []
    cards_as_text: bool = True
    customer: dict | None = None  # {channel, id, name} — برای ارسالِ رسید به گروهِ سفارش‌ها


@app.post("/api/vision")
async def brain_vision(body: VisionIn, x_sb_token: str = Header(None, alias="X-SB-Token")):
    """تشخیصِ عکسِ ساعت → جستجو و کارت. منبعِ واحد برای همهٔ کانال‌ها."""
    _check_sb_token(x_sb_token)
    _cust = body.customer or {}
    _ch, _cid = (_cust.get("channel") or ""), str(_cust.get("id") or "")
    if _ch and _cid and _tg_app:  # حین اتصالِ زندهٔ فعال: عکس را به اپراتور خبر بده، AI پردازش نکند
        import telegram_bot as _tb
        if _tb.is_handoff_active(_ch, _cid):
            try:
                import base64 as _b64
                _img = _b64.b64decode(body.image_b64) if body.image_b64 else b""
                # عکسِ واقعی + سؤالِ همراهِ کاربر (body.caption از نزدیک‌ترین متنِ همان گفتگو ساخته شده) به اپراتور
                await _tb.relay_user_photo_to_group(_tg_app.bot, _ch, _cid, _img, (body.caption or "").strip(), _cust.get("name", ""))
            except Exception as e:  # noqa: BLE001
                print(f"[brain] رله‌ی عکسِ کاربر به گروه ناموفق: {type(e).__name__}: {e}")
            return {"text": "", "cards": [], "handoff": True, "handoff_active": True}
    data_url = ""
    if body.image_b64:
        data_url = f"data:{body.mime or 'image/jpeg'};base64," + body.image_b64.strip()
    elif body.image_url:
        data_url = body.image_url.strip()
    if not data_url:
        raise HTTPException(status_code=400, detail="no image")
    text, ctx = await assistant.answer_image(
        data_url, body.caption, body.messages, render_cards_inline=body.cards_as_text, customer=body.customer)
    receipt = ctx.get("receipt")
    cards = ctx.get("cards") or []
    escalated = False
    cust = body.customer or {}
    if receipt and cust and body.image_b64 and _tg_app:
        # تصویرِ «فیشِ پرداخت» → به گروهِ سفارش‌ها با دکمهٔ تایید/رد
        try:
            import base64 as _b64

            import telegram_bot
            img = _b64.b64decode(body.image_b64)
            extra = " ".join(x for x in (receipt.get("tracking", ""), receipt.get("note", "")) if x).strip()
            await telegram_bot.post_crosschannel_receipt(
                _tg_app.bot, img, cust.get("channel", ""), cust.get("id", ""),
                name=cust.get("name", ""), amount=receipt.get("amount", ""), extra=extra)
        except Exception as e:  # noqa: BLE001
            print(f"[brain] ارسالِ رسیدِ کانالی به گروه ناموفق: {type(e).__name__}: {e}")
    elif ctx.get("ask_staff") and cust and body.image_b64 and _tg_app:
        # فقط وقتی مغز صریحاً درماند (نشانهٔ ASKSTAFF) → ارجاع؛ دیگر catch-all نیست و پاسخِ واقعیِ مغز را دور نمی‌ریزد
        try:
            import base64 as _b64

            import telegram_bot
            img = _b64.b64decode(body.image_b64)
            escalated = await telegram_bot.post_staff_escalation(
                _tg_app.bot, img, cust.get("channel", ""), cust.get("id", ""),
                name=cust.get("name", ""), question=(body.caption or ""))
            if escalated and not (text or "").strip():
                text = ("عکستون رو دیدم 🙏 برای اینکه دقیق راهنماییتون کنم همین الان از همکارانم می‌پرسم و "
                        "تا چند دقیقهٔ دیگه جوابتون رو همین‌جا می‌فرستم 🌟")
        except Exception as e:  # noqa: BLE001
            print(f"[brain] ارجاعِ عکس به همکاران ناموفق: {type(e).__name__}: {e}")
    handoff = ctx.get("handoff")
    return {
        "text": text,
        "cards": cards,
        "receipt": bool(receipt),
        "escalated": bool(escalated),
        "handoff": bool(handoff),
        "handoff_reason": (handoff or {}).get("reason", "") if handoff else "",
    }


class TranscribeIn(BaseModel):
    audio_b64: str = ""
    filename: str = "voice.ogg"


@app.post("/api/transcribe")
async def brain_transcribe(body: TranscribeIn, x_sb_token: str = Header(None, alias="X-SB-Token")):
    """وویس→متن (همان Whisperِ مغز) تا همهٔ کانال‌ها از یک منبعِ واحد استفاده کنند.

    صدا را base64 می‌گیرد (سازگار با کلاینتِ پایتون و Node) و متن را برمی‌گرداند.
    """
    _check_sb_token(x_sb_token)
    import base64
    try:
        data = base64.b64decode(body.audio_b64 or "")
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid audio_b64")
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    try:
        text = await llm.transcribe(data, (body.filename or "voice.ogg"))
    except Exception as e:  # noqa: BLE001
        print(f"[brain] خطای ترنسکرایب: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail="transcription failed")
    return {"text": (text or "").strip()}


@app.get("/", response_class=HTMLResponse)
async def demo_page():
    return _DEMO_HTML


@app.get("/embed.js")
async def embed_js():
    return Response(content=_EMBED_JS, media_type="application/javascript")


def _he(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _hot_lead_alert_loop():
    """مشتریانِ داغِ تازه را به گروهِ «پیگیری مشتریان و CRM» خبر بده (هر ~۲ دقیقه یک چک)."""
    await asyncio.sleep(60)
    _ch = {"whatsapp": "🟢 واتساپ", "instagram": "📸 اینستاگرام", "telegram": "💬 تلگرام", "web": "💻 چت سایت"}
    while True:
        try:
            gid = config.ORDERS_GROUP_ID or config.SUPPORT_GROUP_ID or config.STAFF_GROUP_ID
            if _tg_app and gid:
                import analytics
                coupon = ""
                try:
                    import salescfg
                    coupon = (salescfg.get("first_buyer_coupon") or "").strip()
                except Exception:  # noqa: BLE001
                    pass
                for l in analytics.pop_new_hot_leads():
                    ch = _ch.get(l.get("ch"), l.get("ch") or "—")
                    prods = "، ".join(l.get("products") or [])[:140]
                    name0 = (l.get("name") or "").strip() or "دوستِ عزیز"
                    prod0 = (l.get("products") or [""])[0] if l.get("products") else ""
                    sugg = (f"سلام {name0} 🌹 دیدم به "
                            + (f"«{prod0}» " if prod0 else "ساعتِ موردنظرتون ")
                            + "علاقه داشتید؛ اگر سوالی هست کنارتونم تا بهترین انتخاب رو داشته باشید.")
                    if coupon:
                        sugg += f" ضمناً برای خریدِ اول یک هدیهٔ کوچک هم داریم: کدِ تخفیفِ {coupon} 🎁"
                    txt = ("🔥 <b>مشتریِ داغ — سیگنالِ خریدِ بالا</b>\n\n"
                           f"کانال: {ch}\n"
                           f"مشتری: <b>{_he(l.get('name') or l.get('cid') or '')}</b>\n"
                           f"امتیازِ خرید: <b>{l.get('score')}</b>\n"
                           + (f"موردِعلاقه: {_he(prods)}\n" if prods else "")
                           + f"\n💬 آخرین پیام:\n«{_he((l.get('last_msg') or '')[:220])}»\n\n"
                           "⏱ همین حالا پیگیری کنید — احتمالِ فروش بالاست.\n\n"
                           "💡 <b>پیامِ آمادهٔ پیشنهادی</b> (کپی و ارسال کنید):\n<code>"
                           + _he(sugg) + "</code>")
                    try:
                        await _tg_app.bot.send_message(gid, txt, parse_mode="HTML", disable_web_page_preview=True)
                    except Exception as e:  # noqa: BLE001
                        print(f"[hot-lead] ارسال ناموفق: {e!r}")
        except Exception as e:  # noqa: BLE001
            print(f"[hot-lead] خطای حلقه: {e!r}")
        await asyncio.sleep(120)


async def _post_daily_report_to_tg():
    """خلاصهٔ گزارشِ مدیریتیِ روز را به مدیر(ها) در تلگرام (دایرکت) بفرست."""
    try:
        if not _tg_app:
            return
        import sales_ai
        rp = (sales_ai.latest() or {}).get("report") or {}
        if not rp:
            return
        lines = ["📊 <b>گزارشِ روزانهٔ فروش — گالری جواهریان</b>", ""]
        if rp.get("summary"):
            lines.append(_he(rp["summary"]))
        acts = rp.get("growth_actions") or []
        if acts:
            lines.append("\n🚀 <b>مهم‌ترین اقدام‌های امروز:</b>")
            for a in acts[:3]:
                lines.append(f"• {_he(a.get('title', ''))} <i>({_he(a.get('priority', ''))})</i>")
        if rp.get("risk"):
            lines.append(f"\n⚠️ ریسک: {_he(rp['risk'])}")
        lines.append("\n📱 جزئیاتِ کامل در داشبوردِ مدیریتی.")
        txt = "\n".join(lines)[:3900]
        for uid in (getattr(config, "ADMIN_USER_IDS", []) or []):
            try:
                await _tg_app.bot.send_message(uid, txt, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e:  # noqa: BLE001
                print(f"[sales-ai] ارسالِ گزارش به ادمین {uid} ناموفق: {e!r}")
    except Exception as e:  # noqa: BLE001
        print(f"[sales-ai] خطای ارسالِ گزارشِ روزانه: {e!r}")


async def _product_sync_loop():
    """کاتالوگ را در پس‌زمینه محلی نگه دار: اول اگر لازم بود بگیر، بعد طبقِ بازهٔ تنظیمی تازه کن."""
    await asyncio.sleep(20)
    import productindex
    while True:
        try:
            st = productindex.status()
            stale = (st["count"] == 0) or (st["age_min"] is None) or (st["age_min"] > productindex.refresh_hours() * 60)
            if stale and not st["syncing"]:
                print("[productindex] همگام‌سازیِ کاتالوگ…")
                print("[productindex]", await productindex.sync())
        except Exception as e:  # noqa: BLE001
            print(f"[productindex] خطای حلقه: {e!r}")
        await asyncio.sleep(900)   # هر ۱۵ دقیقه چک (sync خودش طبقِ سن تصمیم می‌گیرد)


async def _daily_analysis_loop():
    """گزارشِ مدیریتیِ فروش را روزی یک‌بار (بعد از ساعتِ مقرر) خودکار بساز + به مدیر در تلگرام بفرست."""
    await asyncio.sleep(90)  # صبر تا سرویس کامل بالا بیاید
    while True:
        try:
            import sales_ai
            if await sales_ai.maybe_run_daily():
                print("[sales-ai] گزارشِ روزانهٔ فروش ساخته شد ✅")
                await _post_daily_report_to_tg()   # خلاصه به مدیر در تلگرام
        except Exception as e:  # noqa: BLE001
            print(f"[sales-ai] خطای حلقهٔ روزانه: {e!r}")
        await asyncio.sleep(1800)


async def _crm_name_sync_loop():
    """صفِ «نامِ نرمال‌شده» را به CRMِ سایت می‌فرستد (سینکِ دوطرفه، نرم و انسانی)."""
    await asyncio.sleep(90)
    import crm_index
    if not getattr(config, "CRM_NAME_UPDATE_URL", ""):
        return
    import httpx
    while True:
        try:
            for it in crm_index.pop_crm_pushes(10):
                payload = {"phone": it["phone"], "first_name": it["first"], "last_name": it["last"]}
                if it.get("tg_id"):
                    payload["telegram_id"] = it["tg_id"]
                try:
                    async with httpx.AsyncClient(timeout=15) as c:
                        await c.post(config.CRM_NAME_UPDATE_URL, json=payload,
                                     headers={"X-A2-Token": config.CRM_NAME_UPDATE_TOKEN})
                except Exception as e:  # noqa: BLE001
                    print(f"[crm-sync] ارسالِ نام ناموفق: {type(e).__name__}")
                crm_index.mark_crm_pushed(it["phone"])
                await asyncio.sleep(2)   # نرم و انسانی
        except Exception as e:  # noqa: BLE001
            print(f"[crm-sync] خطای حلقه: {e!r}")
        await asyncio.sleep(60)


async def _dna_backfill_startup():
    """اگر هنوز پروفایلِ DNA ساخته نشده، خزندهٔ backfill را یک‌بار اجرا کن (از سفارش‌های موجود)."""
    await asyncio.sleep(45)   # بگذار ایندکس/سرویس بالا بیاید
    try:
        import crm_index
        if crm_index.stats().get("persons", 0) > 0:
            return
        print("[dna] خزندهٔ backfill شروع شد (ساختِ DNA از سفارش‌های موجود)…")
        r = await crm_index.crawl_from_woo()
        print(f"[dna] خزنده تمام شد: {r}")
    except Exception as e:  # noqa: BLE001
        print(f"[dna] خطای خزندهٔ backfill: {type(e).__name__}: {e}")


async def serve(tg_app=None):
    global _tg_app
    _tg_app = tg_app
    asyncio.create_task(_daily_analysis_loop())  # زمان‌بندِ تحلیلِ روزانه
    asyncio.create_task(_hot_lead_alert_loop())  # هشدارِ لحظه‌ایِ مشتریِ داغ به گروهِ CRM
    asyncio.create_task(_product_sync_loop())    # ایندکسِ محلیِ محصولات (جستجوی آنی)
    asyncio.create_task(_dna_backfill_startup())  # خزندهٔ DNA: اگر پروفایلی نیست، از سفارش‌های موجود بساز
    asyncio.create_task(_crm_name_sync_loop())    # نوشتنِ برگشتیِ نامِ نرمال‌شده به CRM (سینکِ دوطرفه)
    # log_config=None تا uvicorn لاگینگ را روی stdout بازپیکربندی نکند (با لاگ تهرانِ main تداخل دارد)
    cfg = uvicorn.Config(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning", log_config=None)
    server = uvicorn.Server(cfg)
    print(f"[web] چت سایت روی http://{config.WEB_HOST}:{config.WEB_PORT} فعال شد.")
    await server.serve()


# ---------- ویجت قابل‌جاسازی ----------
# در سایت فقط این خط را قبل از </body> بگذار (آدرس را با دامنه‌ی عمومی عوض کن):
#   <script src="https://CHAT.DOMAIN/embed.js" defer></script>
_EMBED_JS = r"""
(function () {
  var base = (function () {
    var s = document.currentScript;
    if (!s) { var a = document.getElementsByTagName('script'); s = a[a.length - 1]; }
    try { return new URL(s.src).origin; } catch (e) { return ''; }
  })();

  var sid = localStorage.getItem('jg_chat_sid');
  if (!sid) { sid = 'w' + Date.now() + Math.floor(Math.random() * 1e6); localStorage.setItem('jg_chat_sid', sid); }

  var css = ''
    + '#jg-btn{position:fixed;bottom:20px;left:20px;z-index:999999;width:60px;height:60px;border-radius:50%;'
    + 'background:#caa15a;color:#fff;border:none;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25);font-size:26px}'
    + '#jg-box{position:fixed;bottom:90px;left:20px;z-index:999999;width:340px;max-width:92vw;height:480px;max-height:75vh;'
    + 'background:#fff;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.3);display:none;flex-direction:column;'
    + 'overflow:hidden;font-family:Tahoma,sans-serif;direction:rtl}'
    + '#jg-hd{background:#1a1a1a;color:#caa15a;padding:12px 14px;font-weight:bold}'
    + '#jg-msgs{flex:1;overflow-y:auto;padding:12px;background:#f7f7f7}'
    + '.jg-m{margin:6px 0;padding:8px 11px;border-radius:12px;max-width:82%;white-space:pre-wrap;line-height:1.7;font-size:14px}'
    + '.jg-u{background:#caa15a;color:#fff;margin-left:auto}'
    + '.jg-a{background:#fff;border:1px solid #eee;color:#222;margin-right:auto}'
    + '.jg-a a{color:#9a7b2e}'
    + '#jg-in{display:flex;border-top:1px solid #eee}'
    + '#jg-tx{flex:1;border:none;padding:12px;font-family:inherit;font-size:14px;outline:none}'
    + '#jg-snd{border:none;background:#caa15a;color:#fff;padding:0 16px;cursor:pointer;font-size:15px}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  var btn = document.createElement('button'); btn.id = 'jg-btn'; btn.innerHTML = '💬';
  var box = document.createElement('div'); box.id = 'jg-box';
  box.innerHTML = '<div id="jg-hd">مشاورِ ساعتِ گالری جواهریان</div>'
    + '<div id="jg-msgs"></div>'
    + '<div id="jg-in"><input id="jg-tx" placeholder="پیامتون رو بنویسید…" autocomplete="off"><button id="jg-snd">ارسال</button></div>';
  document.body.appendChild(btn); document.body.appendChild(box);

  var msgs = box.querySelector('#jg-msgs');
  var tx = box.querySelector('#jg-tx');
  var greeted = false;

  function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
  function linkify(s){return esc(s).replace(/(https?:\/\/[^\s]+)/g,'<a href="$1" target="_blank">$1</a>');}
  function add(text, who){
    var d=document.createElement('div'); d.className='jg-m '+(who==='u'?'jg-u':'jg-a');
    d.innerHTML = who==='u'?esc(text):linkify(text);
    msgs.appendChild(d); msgs.scrollTop=msgs.scrollHeight; return d;
  }

  function toggle(){
    var open = box.style.display==='flex';
    box.style.display = open?'none':'flex';
    if(!open && !greeted){greeted=true; add('سلام 🌟 به گالری جواهریان خوش اومدید. دنبالِ چه ساعتی هستید؟ با کمالِ میل کمکتون می‌کنم.', 'a'); tx.focus();}
  }
  btn.onclick = toggle;

  function send(){
    var t = tx.value.trim(); if(!t) return;
    tx.value=''; add(t,'u');
    var typing = add('در حال نوشتن…','a');
    var cust = (window.JG_CUSTOMER || {});
    fetch(base + '/chat', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sid, message: t, phone: cust.phone || '', name: cust.name || ''})
    }).then(function(r){return r.json();})
      .then(function(j){ typing.innerHTML = linkify(j.reply || '...'); msgs.scrollTop=msgs.scrollHeight; })
      .catch(function(){ typing.textContent = 'ارتباط برقرار نشد 🙏 لطفاً دوباره تلاش کنید.'; });
  }
  box.querySelector('#jg-snd').onclick = send;
  tx.addEventListener('keydown', function(e){ if(e.key==='Enter'){ e.preventDefault(); send(); }});
})();
"""

_DEMO_HTML = """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>آزمایش دستیار فروش</title>
<style>body{font-family:Tahoma,sans-serif;background:#1a1a1a;color:#eee;text-align:center;padding-top:80px}
h1{color:#caa15a}</style></head>
<body>
<h1>مشاورِ ساعتِ گالری جواهریان</h1>
<p>روی دکمهٔ گفتگو در پایینِ صفحه بزنید و گفتگو رو امتحان کنید.</p>
<script src="/embed.js" defer></script>
</body></html>
"""
