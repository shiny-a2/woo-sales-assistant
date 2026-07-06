"""موتورِ «تحلیلِ فروشِ مدیریتی» — روزی یک‌بار، با مدلِ اختصاصی (پیش‌فرض gpt-5.5).

نگاهِ کلی (نه صرفاً روزانه): همهٔ داده‌ها را جمع می‌کند — سیگنال‌های گفتگو، آمارِ کانال‌ها،
کاربران، و فروشِ واقعیِ سایت (ووکامرس) — و از مدل می‌خواهد یک گزارشِ مدیریتیِ ساختارمند بدهد:
  • نقاط قوت و ضعفِ «مجموعه، سایت و هر کانال»
  • فرصت‌ها و پیشنهادها
  • اقدام‌های عملیِ «ارتقای فروش» در هر زمینه (با اولویت)
همچنین یک «چتِ مدیریتی»: مدیر سوال می‌پرسد و طبقِ همین آمار دقیق پاسخ می‌گیرد.

خروجی در data/sales_report.json (آخرین گزارش) + data/sales_report_hist.json (تاریخچهٔ کوتاه).
اجرا: web_server یک حلقهٔ روزانه دارد که maybe_run_daily را صدا می‌زند.
"""
from __future__ import annotations

import json
import os
import re

import llm
import modelcfg

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPORT = os.path.join(_HERE, "data", "sales_report.json")
_HIST = os.path.join(_HERE, "data", "sales_report_hist.json")

REPORT_HOUR = 9  # ساعتِ تهران که گزارشِ روزانه ساخته می‌شود (اگر امروز هنوز ساخته نشده)
_running = False  # قفلِ نرم تا دو اجرای هم‌زمان نشود
_woo_cache = {"t": 0.0, "data": None}  # کشِ ۱۵ دقیقه‌ایِ فروشِ سایت تا چتِ مدیریتی سریع بمانَد


# ---------------- کمکی‌ها ----------------
def _today():
    try:
        import clock
        return clock.tehran_now().strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        import time
        return time.strftime("%Y-%m-%d")


def _now_h():
    try:
        import clock
        return clock.tehran_now()
    except Exception:  # noqa: BLE001
        import datetime
        return datetime.datetime.now()


def _now_str():
    return _now_h().strftime("%Y-%m-%d %H:%M")


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def _write(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def _extract_json(text):
    """اولین شیءِ JSON را از خروجیِ مدل بیرون بکش (اگر داخلِ ```json بود یا متنِ اضافه داشت)."""
    t = (text or "").strip()
    t = re.sub(r"^```(json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:  # noqa: BLE001
            return None
    return None


# ---------------- جمع‌آوریِ داده ----------------
async def _woo_sales(days=14):
    """خلاصهٔ فروشِ واقعیِ سایت در N روزِ اخیر (best-effort؛ کش‌شده تا سرعتِ چتِ مدیریتی بالا بماند)."""
    import time as _t
    if _woo_cache["data"] is not None and (_t.time() - _woo_cache["t"]) < 900:
        return _woo_cache["data"]
    out = {"days": days, "orders": 0, "revenue_toman": 0, "by_status": {}, "top_products": [], "avg_toman": 0}
    try:
        import datetime
        import woo
        after = (_now_h() - datetime.timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
        rows = await woo.get("orders", {"per_page": 100, "after": after, "orderby": "date", "order": "desc"})
        if not isinstance(rows, list):
            return out
        prod = {}
        paid_total = 0.0
        paid_n = 0
        for o in rows:
            st = str(o.get("status") or "")
            out["by_status"][st] = out["by_status"].get(st, 0) + 1
            try:
                tot = float(o.get("total") or 0)
            except Exception:  # noqa: BLE001
                tot = 0.0
            if st in ("completed", "processing", "on-hold"):
                paid_total += tot
                paid_n += 1
            for li in (o.get("line_items") or []):
                nm = (li.get("name") or "").strip()
                if nm:
                    prod[nm] = prod.get(nm, 0) + int(li.get("quantity") or 1)
        out["orders"] = len(rows)
        # ووکامرسِ این سایت مبلغ را به تومان می‌دهد یا ریال؟ همان خام را می‌گذاریم؛ مدل با احتیاط تفسیر می‌کند.
        out["revenue_toman"] = int(paid_total)
        out["avg_toman"] = int(paid_total / paid_n) if paid_n else 0
        out["top_products"] = [{"name": k, "qty": v} for k, v in
                               sorted(prod.items(), key=lambda kv: kv[1], reverse=True)[:8]]
    except Exception:  # noqa: BLE001
        pass
    _woo_cache["t"] = _t.time()
    _woo_cache["data"] = out
    return out


async def gather():
    """همهٔ داده‌های لازم برای تحلیل را در یک دیکشنری جمع کن."""
    data = {"generated_at": _now_str(), "day": _today()}
    try:
        import analytics
        data["signals"] = analytics.snapshot()
    except Exception:  # noqa: BLE001
        data["signals"] = {}
    try:
        import metrics
        data["metrics"] = metrics.snapshot()
    except Exception:  # noqa: BLE001
        data["metrics"] = {}
    try:
        import botusers
        data["users"] = botusers.counts()
    except Exception:  # noqa: BLE001
        data["users"] = {}
    data["woo"] = await _woo_sales()
    return data


# ---------------- ساختِ گزارش ----------------
_SYS = (
    "تو تحلیل‌گرِ ارشدِ فروش و بازاریابیِ «گالری جواهریانِ» (فروشِ ساعتِ برند، سایت javaherian-gallery.com) هستی. "
    "مدیرانِ مجموعه گزارشِ تو را می‌خوانند تا تصمیمِ بهترِ فروش بگیرند. "
    "فقط بر پایهٔ آمارِ داده‌شده تحلیل کن (چیزی از خودت نساز؛ اگر داده کم بود صادقانه بگو). "
    "همه‌چیز کاملاً فارسی و کاربردی و مدیریتی باشد؛ کلی‌گویی نکن، عدد و مصداق بیاور. "
    "کانال‌ها: واتساپ، اینستاگرام، تلگرام، و چتِ سایت. "
    "خروجی را «فقط» به‌صورتِ یک شیءِ JSON بده (بدونِ توضیحِ اضافه، بدونِ ```)، دقیقاً با این کلیدها:\n"
    "{\n"
    '  "summary": "خلاصهٔ مدیریتی ۳ تا ۵ جمله",\n'
    '  "kpis": [{"label":"...","value":"...","note":"..."}],\n'
    '  "strengths": [{"area":"مجموعه|سایت|واتساپ|اینستاگرام|تلگرام|چت سایت","point":"...","why":"..."}],\n'
    '  "weaknesses": [{"area":"...","point":"...","impact":"...","fix":"..."}],\n'
    '  "opportunities": [{"title":"...","detail":"..."}],\n'
    '  "growth_actions": [{"title":"...","area":"...","detail":"گامِ عملی","priority":"بالا|متوسط|پایین","expected":"اثرِ موردانتظار"}],\n'
    '  "channels": [{"name":"واتساپ|اینستاگرام|تلگرام|سایت","health":"good|ok|weak","note":"...","action":"مهم‌ترین اقدام"}],\n'
    '  "products_insight": "جمع‌بندیِ محصول/برندِ پرتقاضا و کم‌فروش و پیشنهادِ چیدمان/تخفیف",\n'
    '  "risk": "مهم‌ترین ریسک یا هشدار"\n'
    "}\n"
    "حداقل ۳ موردِ قوت، ۳ ضعف و ۴ اقدامِ ارتقای فروش بده. اولویت‌ها واقع‌بینانه باشند."
)


def _data_blob(data):
    return "آمار و دادهٔ فعلیِ مجموعه (JSON):\n" + json.dumps(data, ensure_ascii=False)


async def run_analysis(model=None):
    """گزارشِ مدیریتیِ کامل را بساز، ذخیره کن و برگردان."""
    global _running
    if _running:
        return latest()
    _running = True
    try:
        data = await gather()
        mdl = (model or modelcfg.analysis_model())
        messages = [
            {"role": "system", "content": _SYS},
            {"role": "user", "content": _data_blob(data) +
             "\n\nحالا گزارشِ مدیریتیِ کاملِ فروش را طبقِ ساختارِ خواسته‌شده بده."},
        ]
        raw = await llm.complete(messages, model=mdl, max_tokens=9000, effort="medium")
        report = _extract_json(raw) or {}
        rec = {
            "ok": bool(report),
            "generated_at": _now_str(),
            "day": _today(),
            "model": mdl,
            "report": report,
            "raw": "" if report else (raw or "")[:4000],  # اگر JSON نشد، خام را برای دیباگ نگه دار
            "data": data,
        }
        _write(_REPORT, rec)
        hist = _read(_HIST, [])
        if not isinstance(hist, list):
            hist = []
        hist.insert(0, {"generated_at": rec["generated_at"], "day": rec["day"],
                        "summary": (report.get("summary") if report else "")})
        _write(_HIST, hist[:30])
        return rec
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "generated_at": _now_str(), "day": _today()}
    finally:
        _running = False


def latest():
    rec = _read(_REPORT, None)
    if not rec:
        return {"ok": False, "empty": True, "report": {}, "generated_at": "", "day": ""}
    return rec


def history():
    h = _read(_HIST, [])
    return h if isinstance(h, list) else []


# ---------------- زمان‌بندِ روزانه ----------------
async def maybe_run_daily():
    """گزارش را «حداکثر یک‌بار در روز» بساز — مدیریتِ مصرفِ موتورِ ۵.۵ (یک ریکوئست در روز، حتی اگر شکست بخورد)."""
    try:
        rec = _read(_REPORT, None)
        today = _today()
        if rec and rec.get("day") == today:
            return False   # امروز تلاش/ساخته شده → دیگر به API نزن (کنترلِ هزینه)
        if _now_h().hour < REPORT_HOUR:
            return False
        # روزِ تلاش را پیش از فراخوانی ثبت کن تا اگر شکست خورد هم امروز دوباره API نخورَد
        _write(_REPORT, {"ok": False, "day": today, "generated_at": _now_str(), "model": "", "report": {}, "pending": True})
        rec = await run_analysis()
        return bool(rec and rec.get("ok"))
    except Exception:  # noqa: BLE001
        return False


# ---------------- چتِ مدیریتی ----------------
async def ask(question, model=None, history_msgs=None, image_b64=None, mime="image/jpeg"):
    """به سوالِ مدیر «طبقِ آمار» پاسخ بده. از آخرین گزارش + دادهٔ زندهٔ فعلی استفاده می‌کند."""
    data = await gather()
    rec = latest()
    rep = rec.get("report") if isinstance(rec, dict) else {}
    sys = (
        "تو تحلیل‌گرِ فروشِ گالری جواهریانی و به سوالِ «مدیر» پاسخ می‌دهی. "
        "فقط بر پایهٔ «آمار و گزارشِ» داده‌شده پاسخ بده؛ عدد و مصداق بیاور و کوتاه و دقیق باش. "
        "اگر داده برای پاسخِ دقیق کافی نبود، صادقانه بگو چه داده‌ای لازم است. فارسیِ خالص. "
        "اگر سوال دربارهٔ ارتقای فروش بود، پیشنهادِ عملی بده.\n\n"
        "== دادهٔ زندهٔ فعلی ==\n" + json.dumps(data, ensure_ascii=False) +
        "\n\n== آخرین گزارشِ مدیریتی ==\n" + json.dumps(rep, ensure_ascii=False)
    )
    messages = [{"role": "system", "content": sys}]
    for m in (history_msgs or [])[-6:]:
        r = m.get("role")
        c = (m.get("content") or "").strip()
        if r in ("user", "assistant") and c:
            messages.append({"role": r, "content": c})
    if image_b64:   # مدیر فایل/تصویری پیوست کرده → تحلیل‌گر با نگاهِ فروش نگاهش می‌کند
        data_url = f"data:{mime or 'image/jpeg'};base64," + image_b64.strip()
        messages.append({"role": "user", "content": [
            {"type": "text", "text": (question or "").strip() or "این تصویر را از منظرِ فروش/بازار تحلیل کن."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]})
    else:
        messages.append({"role": "user", "content": (question or "").strip()})
    # چتِ مدیریتی با «مدلِ چتِ ارزان‌تر» (نه ۵.۵) تا مصرفِ موتورِ گران فقط برای گزارشِ روزانه بماند
    ans = await llm.complete(messages, model=(model or modelcfg.chat_model()), max_tokens=2500, effort="low")
    return ans or "پاسخی تولید نشد."
