"""سیستمِ سلامتِ سراسری — لاگِ فوق‌سبکِ ساختارمند + هلث‌چکِ روزانهٔ کانال‌ها + بازبینیِ هوشمندِ روزانه.

سه تکه:
  ۱) لاگِ خطا/رویداد (JSONL فوق‌سبک، data/health.jsonl) — از هر جای مغز + سرویس‌های دیگر (HTTP).
  ۲) هلث‌چکِ رفتاریِ روزانهٔ همهٔ کانال‌ها (تلگرام/واتساپ/اینستا/چتِ سایت/یوزربات/پیامک/فالوآپ)
     → نتیجه در data/health_status.json → تیکِ سلامت در «خلاصهٔ» داشبورد.
  ۳) بازبینیِ روزانه با gpt-5.5 (تینکینگ): لاگ + متریک‌ها + «نمونه‌های واقعیِ پاسخِ اپراتورِ انسانی» را
     می‌خواند، ضعف‌ها را می‌یابد و «آموخته‌های روزانه» (هر تعداد نکتهٔ رفتاریِ باکیفیت، بدونِ سقف) تولید
     می‌کند که به پرسونا تزریق می‌شود → خودبهبودی بر پایهٔ سبکِ فروشِ انسانی.
"""
from __future__ import annotations

import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG = os.path.join(_HERE, "data", "health.jsonl")
_STATUS = os.path.join(_HERE, "data", "health_status.json")
_LESSONS = os.path.join(_HERE, "data", "self_improve.json")
_OPS_EX = os.path.join(_HERE, "data", "operator_examples.jsonl")  # پیام‌های اپراتورِ انسانی → سوختِ یادگیری
_MAX_LOG_BYTES = 2 * 1024 * 1024   # ~۲MB؛ بعدش نصفِ قدیمی حذف می‌شود (فوق‌سبک بماند)
_MAX_OPS_EX = 1500                  # آخرین N نمونهٔ اپراتور نگه داشته می‌شود (چرخشی)


def record_operator_example(channel, customer_id, customer_msg, operator_msg, kind="in_platform"):
    """یک «پاسخِ اپراتورِ انسانی» را به‌عنوان نمونهٔ طلاییِ یادگیری ثبت می‌کند.

    kind: "live" (اتصالِ زندهٔ رله‌شده از گروه) یا "in_platform" (اپراتور مستقیم در خودِ واتساپ/تلگرام جواب داد).
    این‌ها سوختِ «بازبینیِ هوشمند»‌اند تا مغز از سبکِ واقعیِ فروشِ انسانی بیاموزد.
    """
    op = (operator_msg or "").strip()
    if not op or len(op) < 2:
        return False
    if op.startswith((".", "/", "!")) or op in ("بات", "پایان", "ادامه"):
        return False   # دستورِ کنترلیِ اپراتور، نه پاسخِ فروش
    rec = {"t": _now_str(), "channel": channel or "", "cid": str(customer_id or ""),
           "kind": kind, "customer": (customer_msg or "").strip()[:600], "operator": op[:900]}
    try:
        os.makedirs(os.path.dirname(_OPS_EX), exist_ok=True)
        # چرخشی: اگر بیش از حد شد، نیمهٔ قدیمی را دور بریز
        lines = []
        if os.path.exists(_OPS_EX):
            with open(_OPS_EX, encoding="utf-8") as f:
                lines = f.readlines()
        lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
        if len(lines) > _MAX_OPS_EX:
            lines = lines[-_MAX_OPS_EX:]
        tmp = _OPS_EX + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp, _OPS_EX)
        return True
    except Exception as e:  # noqa: BLE001
        log("ops-example", "warn", f"{type(e).__name__}: {e}")
        return False


def recent_operator_examples(n=50, since_hours=None):
    """آخرین N نمونهٔ پاسخِ اپراتور (اختیاراً فقط در بازهٔ اخیر) — برای تزریق به بازبینیِ هوشمند."""
    try:
        if not os.path.exists(_OPS_EX):
            return []
        with open(_OPS_EX, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        return rows[-n:]
    except Exception:  # noqa: BLE001
        return []


def _env_of(service_dir, *keys):
    """خواندنِ یک کلید از .envِ سرویسِ همسایه (توکنِ داشبوردِ wa/ig) — بدونِ وابستگیِ جدید."""
    try:
        path = os.path.join(os.path.dirname(_HERE), service_dir, ".env")
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                for k in keys:
                    if ln.startswith(k + "="):
                        return ln.split("=", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _now_str():
    try:
        import clock
        return clock.tehran_now().strftime("%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return time.strftime("%m-%d %H:%M:%S")


def log(service, level, message):
    """ثبتِ یک رویداد/خطا — فوق‌سبک، هرگز exception نمی‌دهد."""
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        rec = {"t": _now_str(), "ts": time.time(), "svc": str(service or "?")[:24],
               "lvl": str(level or "info")[:8], "msg": str(message or "")[:400]}
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if os.path.getsize(_LOG) > _MAX_LOG_BYTES:
            with open(_LOG, encoding="utf-8") as f:
                lines = f.readlines()
            with open(_LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
    except Exception:  # noqa: BLE001
        pass


def recent(limit=200, level=None):
    try:
        with open(_LOG, encoding="utf-8") as f:
            lines = f.readlines()[-int(limit) * 3:]
        out = []
        for ln in lines:
            try:
                r = json.loads(ln)
                if level and r.get("lvl") != level:
                    continue
                out.append(r)
            except Exception:  # noqa: BLE001
                continue
        return out[-int(limit):]
    except Exception:  # noqa: BLE001
        return []


def _fu_count_24h(svc):
    """تعدادِ فالوآپ‌های ارسال‌شدهٔ ۲۴ ساعتِ اخیرِ یک کانال (از لاگِ واحد)."""
    cutoff = time.time() - 24 * 3600
    return sum(1 for r in recent(800) if r.get("svc") == svc and r.get("lvl") == "sent"
               and float(r.get("ts", 0)) >= cutoff)


# ---------------- هلث‌چکِ رفتاریِ روزانهٔ کانال‌ها ----------------
async def run_channel_checks():
    """یک پیام/پروبِ تستِ رفتاری به هر کانال؛ نتیجه: {channel: {ok, note}}."""
    import httpx
    import config
    res = {}

    async def _mark(name, ok, note=""):
        res[name] = {"ok": bool(ok), "note": str(note)[:120]}
        if not ok:
            log("healthcheck", "error", f"{name}: {note}")

    # ۱) چتِ سایت + مغز + LLM (یک پیامِ تستِ واقعی — پاسخ باید فارسی و غیرخالی باشد)
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"http://127.0.0.1:{config.WEB_PORT}/chat",
                             json={"session_id": "healthcheck", "message": "سلام"})
            t = (r.json().get("reply") or "")
            await _mark("site_chat", bool(t.strip()), f"reply_len={len(t)}")
    except Exception as e:  # noqa: BLE001
        await _mark("site_chat", False, f"{type(e).__name__}: {e}")

    # ۲) رباتِ تلگرام (اتصالِ باتِ زنده)
    try:
        from web_server import _tg_app
        me = await _tg_app.bot.get_me() if _tg_app else None
        await _mark("telegram_bot", bool(me), getattr(me, "username", "") or "no app")
    except Exception as e:  # noqa: BLE001
        await _mark("telegram_bot", False, f"{type(e).__name__}")

    # ۳) واتساپ (اتصال + فالوآپ روشن)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("http://127.0.0.1:8093/api/state",
                            params={"token": _env_of("wa-assistant", "DASH_TOKEN", "WA_DASH_TOKEN", "TOKEN")
                                    or "wa9Xb3Qm7Lr2Tn8Kp4Vz"})   # پیش‌فرضِ داشبوردِ wa وقتی .env خالی است
            d = r.json()
            await _mark("whatsapp", bool(d.get("ready")), f"followup={d.get('followup')}")
            await _mark("followup_wa", bool(d.get("followup")), f"ارسالِ ۲۴س: {_fu_count_24h('followup_wa')}")
    except Exception as e:  # noqa: BLE001
        await _mark("whatsapp", False, f"{type(e).__name__}")
        await _mark("followup_wa", False, "unreachable")

    # ۴) اینستاگرام (لاگین + موتور + فالوآپ)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("http://127.0.0.1:8092/api/state",
                            params={"token": _env_of("ig-assistant", "DASH_TOKEN", "IG_DASH_TOKEN", "TOKEN")})
            s = (r.json() or {}).get("stats", {})
            await _mark("instagram", str(s.get("logged_in")) in ("1", "True", "true") and s.get("engine") == "running",
                        f"engine={s.get('engine')}")
            await _mark("followup_ig", s.get("followup_engine") == "on", f"ارسالِ ۲۴س: {_fu_count_24h('followup_ig')}")
    except Exception as e:  # noqa: BLE001
        await _mark("instagram", False, f"{type(e).__name__}")
        await _mark("followup_ig", False, "unreachable")
    # فالوآپِ تلگرام (مکانیزمِ داخلیِ مغز؛ شمارشِ ارسال‌های ۲۴ ساعت)
    await _mark("followup_tg", True, f"ارسالِ ۲۴س: {_fu_count_24h('followup_tg')}")

    # ۵) یوزربات (پاسخ‌گویی روشن)
    try:
        import sqlite3
        c = sqlite3.connect("file:" + os.path.join(os.path.dirname(_HERE), "tg-outreach", "data", "outreach.db")
                            + "?mode=ro", uri=True)
        ar = c.execute("SELECT value FROM meta WHERE key='autoreply'").fetchone()
        c.close()
        await _mark("userbot", bool(ar and ar[0] == "on"), f"autoreply={ar[0] if ar else '?'}")
    except Exception as e:  # noqa: BLE001
        await _mark("userbot", False, f"{type(e).__name__}")

    # ۶) پیامک (کانفیگ + آخرین خطای بازیابی)
    try:
        with open(os.path.join(os.path.dirname(_HERE), "wa-assistant", "data", "sms.json"), encoding="utf-8") as f:
            sc = json.load(f)
        await _mark("sms", bool(sc.get("enabled") and sc.get("apiKey")),
                    f"tmpl={sc.get('stage1', {}).get('template', '')}")
    except Exception as e:  # noqa: BLE001
        await _mark("sms", False, f"{type(e).__name__}")

    out = {"at": _now_str(), "ts": time.time(), "checks": res,
           "ok_count": sum(1 for v in res.values() if v["ok"]), "total": len(res)}
    try:
        tmp = _STATUS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, _STATUS)
    except Exception:  # noqa: BLE001
        pass
    return out


def status():
    try:
        with open(_STATUS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"at": "", "checks": {}, "ok_count": 0, "total": 0}


# ---------------- بازبینیِ هوشمندِ روزانه (خودبهبودی) ----------------
def lessons():
    try:
        with open(_LESSONS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"date": "", "notes": [], "report": ""}


def lessons_prompt():
    """«آموخته‌های روزانه» برای تزریق به پرسونا (هر تعداد که بازبینی تولید کرد) — خالی اگر نبود."""
    d = lessons()
    notes = [n for n in (d.get("notes") or []) if n and len(n) < 400]
    if not notes:
        return ""
    return ("📘 آموخته‌های فروش (از بازبینیِ هوشمندِ خودکار بر پایهٔ دادهٔ واقعی و پاسخ‌های اپراتورِ انسانی — "
            "این‌ها را در گفتگو رعایت کن):\n- " + "\n- ".join(notes))


async def daily_review():
    """روزی یک‌بار: لاگِ سلامت + متریک‌ها → gpt-5.5 → نقاطِ ضعف + آموخته‌های رفتاریِ کوتاه."""
    try:
        import llm
        import metrics
        errs = recent(120, level="error") + recent(60, level="warn")
        errs = errs[-100:]
        chk = status()
        m = {}
        try:
            m = metrics.snapshot() if hasattr(metrics, "snapshot") else {}
        except Exception:  # noqa: BLE001
            pass
        ops_ex = recent_operator_examples(60)   # پاسخ‌های واقعیِ اپراتورِ انسانی (نمونهٔ طلایی)
        sys_p = (
            "تو ترکیبی از سه نقشِ ارشدِ گالری جواهریان هستی و هم‌زمان با هر سه کلاه فکر می‌کنی:\n"
            "  • «مدیرِ ارشدِ مارکتینگ (CMO)» — به قیفِ فروش، نرخِ تبدیل و اثرِ اقتصادیِ هر رفتار نگاه می‌کنی.\n"
            "  • «رفتارشناسِ مشتری» — انگیزه، تردید، ترس، اعتماد و محرک‌های تصمیمِ خرید را می‌خوانی.\n"
            "  • «استادِ فروشِ ساعتِ لوکس» — می‌دانی یک فروشندهٔ خبره در هر لحظه چه می‌گوید تا معامله پیش برود.\n\n"
            "هر روز به دادهٔ واقعیِ دیروز نگاه می‌کنی: لاگِ خطاها، سلامتِ کانال‌ها، متریک‌های فروش/رفتار، و مهم‌تر از همه "
            "«نمونه‌های واقعیِ پاسخِ اپراتورِ انسانی» به مشتری‌ها. اپراتورِ انسانی استانداردِ طلاییِ لحن و تکنیکِ فروش است؛ "
            "تفاوتِ سبکِ او با پاسخِ ربات را استخراج کن و به دستورِ رفتاریِ قابلِ‌اجرا تبدیل کن.\n\n"
            "این لنزها را به‌کار ببر:\n"
            "  ۱) کجا و چرا مشتری از دست می‌رود (نقطهٔ ریزش در قیف)؟\n"
            "  ۲) کدام جملهٔ ربات نرخِ تبدیل را پایین می‌آورد یا رباتیک/سرد است و اپراتور چطور گرم‌تر گفته؟\n"
            "  ۳) الگوهای رفتاریِ تکرارشونده (تردید، حساسیتِ قیمتی، مقایسه با رقیب، رهاکردنِ سبد، سکوت پس از دیدنِ محصول) "
            "و پاسخِ درستِ هرکدام.\n"
            "  ۴) مدیریتِ اعتراض (قیمت، اصالت، گارانتی، تحویل) — بهترین قابِ پاسخ.\n"
            "  ۵) اهرم‌های اخلاقیِ فروش (کمیابی/فوریتِ واقعی، اثباتِ اجتماعی، ارزش‌سازی، اقساط/اسنپ‌پی به‌جا) بدونِ فشارِ آزاردهنده.\n"
            "  ۶) لحن و انسانی‌بودن: کوتاه، گرم، مطمئن، فارسیِ روان؛ پرهیز از رباتیک‌بودن و تکرار.\n"
            "  ۷) پیگیریِ درست و بستنِ معامله (call-to-action روشن به‌سمتِ خریدِ آنلاین از همین کانال).\n"
            "  ۸) تفاوت‌های کانالی (واتساپ/تلگرام/اینستا/سایت) اگر در داده دیده شد.\n\n"
            "اصلِ اولویت: اثرِ واقعیِ فروش > زیبایی. هر «آموخته» باید مشخص، عملیاتی و مستقیماً قابلِ اجرا در جملهٔ بعدیِ "
            "دستیار باشد (نه توصیهٔ کلی، نه کارِ فنی/زیرساختی). سقفی روی تعدادِ آموخته‌ها نیست: هر تعداد نکتهٔ باکیفیت که از "
            "دادهٔ واقعی پشتیبانی می‌شود بده — نه کمتر، نه پُرکردنِ الکی؛ آموخته‌های تکراری/بی‌پشتوانه نده.\n\n"
            "خروجی فقط JSON با این ساختار: "
            '{"summary":"جمع‌بندیِ مدیریتیِ فارسی ≤۴ جمله","issues":["مشکلاتِ مشاهده‌شده با ذکرِ شواهد"],'
            '"notes":["هر تعداد دستورِ رفتاریِ کوتاه، دقیق و قابلِ‌اجرا برای دستیارِ فروش — با منطقِ فروش/رفتارشناسی، '
            'ترجیحاً برگرفته از سبکِ اپراتورِ انسانی؛ هر نکته یک جملهٔ عملیاتی"]}')
        user_p = ("لاگِ خطاهای اخیر:\n" + json.dumps(errs[-60:], ensure_ascii=False)[:5000]
                  + "\n\nوضعیتِ کانال‌ها:\n" + json.dumps(chk, ensure_ascii=False)[:1200]
                  + "\n\nمتریک‌ها:\n" + json.dumps(m, ensure_ascii=False)[:1200]
                  + "\n\n🟡 نمونه‌های واقعیِ پاسخِ اپراتورِ انسانی (استانداردِ طلایی — از سبک، لحن و تکنیکِ این‌ها بیاموز و "
                    "به آموخته‌های رفتاری تبدیل کن؛ اگر خالی بود فقط بر پایهٔ متریک‌ها تحلیل کن):\n"
                  + json.dumps(ops_ex, ensure_ascii=False)[:9000])
        resp = await llm._create([{"role": "system", "content": sys_p},
                                  {"role": "user", "content": user_p}],
                                 with_tools=False, model="gpt-5.5")
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        d = json.loads(raw)
        prev = lessons()   # تاریخچهٔ تغییرات: هر روز چه آموخته‌هایی اعمال شد (لاگِ تغییراتِ خودبهبودی)
        hist = (prev.get("history") or [])
        if prev.get("date") and prev.get("notes"):
            hist.append({"date": prev["date"], "notes": prev["notes"], "report": prev.get("report", "")})
        out = {"date": _now_str(), "report": d.get("summary", ""), "issues": d.get("issues", [])[:10],
               "notes": [str(n)[:320] for n in (d.get("notes") or [])],   # بدونِ سقف — هر تعداد آموخته
               "history": hist[-30:]}
        tmp = _LESSONS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, _LESSONS)
        # لاگِ صریحِ «تغییرِ رفتار» — چه چیزی جایگزینِ چه چیزی شد
        log("self-improve", "change", "آموخته‌های جدید: " + " | ".join(out["notes"])[:300])
        return out
    except Exception as e:  # noqa: BLE001
        log("self-improve", "error", f"{type(e).__name__}: {e}")
        return None
