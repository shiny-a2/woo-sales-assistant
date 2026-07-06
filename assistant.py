"""هسته‌ی دستیار: پیام کاربر → حلقه‌ی جی‌پی‌تی+ابزار → پاسخ.

مستقل از کانال (تلگرام/وب)؛ هر کانال فقط reply() را صدا می‌زند.
"""
from __future__ import annotations

import time

import analytics
import llm
import metrics
import modelcfg
import persona
import sessions
import textfmt
import woo

_FALLBACK = "ببخشید، یک اشکالِ فنیِ کوچک پیش اومد 🙏 لطفاً چند لحظهٔ دیگه دوباره بفرمایید؛ در خدمتم."


def _name_hint(user_name):
    nm = (user_name or "").strip()
    if not nm:
        return None
    return {"role": "system", "content": f"نامِ تلگرامیِ این کاربر: «{nm}». او را با همین نام و محترمانه صدا بزن، نه اسمِ دیگری."}


def _phone_hint(phone):
    p = (phone or "").strip()
    if not p:
        return None
    return {"role": "system", "content": f"شمارهٔ تماسِ این مشتری از قبل موجود است: «{p}». "
            "برای ثبتِ سفارش/پیگیری از همین شماره استفاده کن و دوباره شماره نپرس."}


def _last_user_text(messages):
    for m in reversed(messages or []):
        c = m.get("content")
        if m.get("role") == "user" and isinstance(c, str) and c.strip():
            return c
    return ""


# سیگنال‌های اضافیِ عملکرد (کلیدواژه‌ای)
_REPAIR_KW = ["تعمیر", "تعمير", "خرابی", "خراب شده", "خرابه", "کار نمیکنه", "کار نمی‌کند", "کار نمیکند",
              "ایراد", "مشکل پیدا", "باتری تموم", "باتریش", "تعویض باتری", "عقربه", "بند پاره", "بندش پاره",
              "شیشه شکست", "شیشه‌اش", "خوابیده", "کند شده", "عقب می‌کشه", "جلو می‌کشه"]
_SELL_KW = ["بفروشم", "می‌فروشم", "میفروشم", "بفروش", "ساعتم رو بخر", "ساعتمو بخر", "ساعتم را بخر",
            "می‌خرید ساعت", "میخرید ساعتم", "ساعتم رو می‌خرید", "ساعتمو می‌خرید", "دست دوم بفروش",
            "کارکرده بفروش", "ساعتِ خودم رو بفروش", "فروشِ ساعتم"]
_STORE_REFERRAL_KW = ["حضوری", "به شعبه", "شعبهٔ", "شعبه مرکزی", "مراجعه حضوری", "تشریف بیارید",
                      "تشریف بیاورید", "تشریف بیارین", "از نزدیک", "حضوراً", "به فروشگاه مراجعه"]


def _record_metrics(channel, ctx, user_text="", name="", cid="", image=False, answer=""):
    """شمردنِ عملکرد + تحلیلِ رفتارِ مشتری: پاسخ/کانال + رویدادهای کلیدی (سفارش/رسید/ارجاع/مدیا) + سیگنال‌های فروش."""
    try:
        ch = str(channel or "")
        metrics.bump("reply", ch)
        if image:
            metrics.bump("image", ch)
        if ctx.get("order"):
            metrics.bump("order", ch)
        if ctx.get("handoff"):
            metrics.bump("handoff", ch)
        if ctx.get("receipt"):
            metrics.bump("receipt", ch)
        if ctx.get("wrist_media") or ctx.get("wrist_media_request"):
            metrics.bump("wrist_media", ch)
        _u, _a = (user_text or ""), (answer or "")
        if any(k in _u for k in _REPAIR_KW):          # درخواستِ تعمیر
            metrics.bump("repair", ch)
        if any(k in _u for k in _SELL_KW):            # قصدِ فروشِ ساعتِ مشتری به ما
            metrics.bump("sell_intent", ch)
        if any(k in _a for k in _STORE_REFERRAL_KW):  # ارجاع به فروشگاهِ حضوری (در پاسخِ مغز)
            metrics.bump("store_referral", ch)
        import salescfg
        _abk, _ = salescfg.ab_assign(f"{ch}:{cid}")
        if _abk:   # آزمونِ A/B لحن (اگر روشن باشد): پاسخ و سفارش را به‌تفکیکِ گروه بشمار
            metrics.bump("ab", _abk)
            if ctx.get("order"):
                metrics.bump("ab_order", _abk)
    except Exception:  # noqa: BLE001
        pass
    try:
        analytics.record(channel, cid, name, user_text, ctx)
    except Exception:  # noqa: BLE001
        pass
    try:
        import crm_index
        crm_index.touch(channel, cid, name)   # افزودنِ خودکارِ هویتِ مخاطب به ایندکسِ کانتکت‌ها
    except Exception:  # noqa: BLE001
        pass


def _greeting_hint(channel, user_id):
    """راهنمای سلامِ ۶ساعته بر اساسِ فاصله از آخرین فعالیتِ همین گفتگو (همهٔ کانال‌ها)."""
    if not (channel and user_id):
        return ""
    prev = sessions.touch_activity(str(channel), str(user_id))
    if prev is not None and (time.time() - prev) < 6 * 3600:
        return ("⏳ وضعیتِ گفتگو: ادامه‌دار (کمتر از ۶ ساعت از پیامِ قبلی گذشته). پس **دوباره سلام/احوال‌پرسی نکن**؛ "
                "مستقیم و طبیعی ادامه بده و به پیام‌های قبلیِ همین گفتگو (ارسالی و دریافتی) دقت کن تا پاسخت دقیق باشد.")
    return "⏳ وضعیتِ گفتگو: تازه یا پس از وقفهٔ بیش از ۶ ساعت. پاسخت را با یک سلامِ گرمِ کوتاهِ متناسبِ زمان شروع کن."


def _ab_extra(channel, uid):
    """متنِ اضافهٔ لحن برای آزمونِ A/B (خاموش = رشتهٔ خالی → بدونِ اثر روی پاسخ)."""
    try:
        import salescfg
        _, txt = salescfg.ab_assign(f"{channel}:{uid}")
        return ("\n\n" + txt) if txt else ""
    except Exception:  # noqa: BLE001
        return ""


# متنِ روی خودِ تصویر (کدِ رفرنس/برند/مدل) اغلب کلیدِ پیدا کردنِ دقیقِ محصول است → حتماً خوانده شود
_IMAGE_TEXT_HINT = (
    " 🔎 اگر روی خودِ تصویر متنی هست (کدِ رفرنس/مدل مثلِ «BF2018-52E» یا «DK.1.14002-5»، نامِ برند، یا نوشتهٔ روی صفحه/قاب)"
    " آن را با دقت بخوان: اگر کد/رفرنس دیدی حتماً اول با find_by_reference همان را جستجو کن؛ و نامِ برندِ روی تصویر را هم در جستجو لحاظ کن.")


async def reply(channel, user_id, text, user_name=None, customer_phone=None):
    """یک پیام را پاسخ می‌دهد.

    خروجی: (متن پاسخ، ctx) که ctx ممکن است شامل {"handoff": {...}} باشد.
    """
    text = (text or "").strip()
    ctx: dict = {}
    if not text:
        return ("سلام 🌟 در خدمتم؛ چطور می‌تونم کمکتون کنم؟", ctx)

    _sys = persona.system_prompt() + _ab_extra(channel, user_id)
    _gh = _greeting_hint(channel, user_id)
    if _gh:
        _sys = _sys + "\n\n" + _gh
    messages = [{"role": "system", "content": _sys}]
    hint = _name_hint(user_name)
    if hint:
        messages.append(hint)
    ph = _phone_hint(customer_phone)
    if ph:
        messages.append(ph)
    if customer_phone:   # سابقهٔ خریدِ مشتری (کش‌شده) را به مغز بده تا شخصی‌سازی کند
        try:
            import crm_index
            _hh = await crm_index.history_hint(channel, user_id, customer_phone)
            if _hh:
                messages.append({"role": "system", "content": _hh})
        except Exception:  # noqa: BLE001
            pass
    messages.extend(sessions.history(channel, user_id))
    messages.append({"role": "user", "content": text})

    ctx["shown_ids"] = list(sessions.shown_ids(channel, user_id))
    try:
        answer = await llm.chat(messages, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] خطا در پاسخ‌دهی: {type(e).__name__}: {e}")
        return (_FALLBACK, ctx)

    if not answer:
        answer = _FALLBACK
    answer = textfmt.clean_for_chat(answer)
    if ctx.get("cards"):  # کارت‌ها جدا (عکس) نمایش داده می‌شوند؛ از متن حذفشان کن
        answer = textfmt.strip_product_lines(answer) or "چند گزینهٔ خوب و مناسب براتون پیدا کردم 🌟 در ادامه ببینید:"

    # فقط در صورت موفقیت، تاریخچه را ذخیره کن
    sessions.append(channel, user_id, "user", text)
    sessions.append(channel, user_id, "assistant", answer)
    sessions.add_shown(channel, user_id, [c.get("id") for c in ctx.get("cards", [])])
    _record_metrics(channel, ctx, text, user_name, user_id, answer=answer)
    return (answer, ctx)


async def reply_image(channel, user_id, image_data_url, caption="", user_name=None):
    """پاسخ به یک تصویر ساعت: شناسایی و پیشنهاد همان/مشابه‌ها."""
    ctx: dict = {"shown_ids": list(sessions.shown_ids(channel, user_id))}
    user_text = ((caption or "").strip() + " ").strip()
    user_text += (" ابتدا تصویر را بررسی کن: اگر **فیش/رسیدِ پرداختِ بانکی** است (نه ساعت)، جستجوی ساعت نکن"
                  " و حتماً ابزارِ payment_receipt را صدا بزن، بعد بگو «رسیدِ پرداختتون دریافت شد ✅ همکاران بررسی"
                  " می‌کنن و نتیجهٔ تأیید رو خدمتتون اعلام می‌کنیم 🙏»، و اگر مبلغ/تاریخ/شمارهٔ پیگیری خواناست کوتاه بازگو کن.")
    user_text += " اما اگر **ساعت** است: این ساعت را از روی تصویر شناسایی کن (جنسیت، رنگ، استایل، برند اگر پیداست) و با search_watches همان یا مشابه‌هایش را پیدا کن، بعد حتماً با show_products به‌صورت کارت نشان بده."
    user_text += (" اما اگر روشن است که ساعت است ولی **مطمئن نیستی زنانه است یا مردانه**"
                  " (مثلاً قابِ متوسط یا مدلی بینِ زنانه و مردانه)، **هیچ ساعتی نشان نده، گمانه‌زنی نکن و سراغِ همکاران نرو**؛"
                  " رنگ/استایل/برندی که از تصویر فهمیدی را کوتاه بگو و حتماً همین عبارت را در سؤالت بیاور:"
                  " «این ساعت رو برای خانم می‌خواید یا آقا؟»، و بعد از جوابِ مشتری با همان مشخصاتِ تصویر + جنسیت جستجو کن."
                  " این فقط برای ابهامِ زنانه/مردانه است؛ اگر برند/مدل یا موجودی‌اش نامعلوم بود، مثلِ قبل به همکاران ارجاع بده.")
    user_text += _IMAGE_TEXT_HINT

    _sys = persona.system_prompt() + _ab_extra(channel, user_id)
    _gh = _greeting_hint(channel, user_id)
    if _gh:
        _sys = _sys + "\n\n" + _gh
    messages = [{"role": "system", "content": _sys}]
    hint = _name_hint(user_name)
    if hint:
        messages.append(hint)
    messages.extend(sessions.history(channel, user_id))
    messages.append({"role": "user", "content": [
        {"type": "text", "text": user_text},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]})

    try:
        answer = await llm.chat(messages, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] خطا در reply_image: {type(e).__name__}: {e}")
        return (_FALLBACK, ctx)

    answer = textfmt.clean_for_chat(answer) or _FALLBACK
    if ctx.get("cards"):
        answer = textfmt.strip_product_lines(answer) or "چند ساعتِ نزدیک به تصویری که فرستادید پیدا کردم 🌟 ببینید:"

    # اگر فقط جنسیت را پرسیده (نه کارت، نه رسید) → فلگ بزن تا ساختار ارجاع‌به‌همکاران رخ ندهد.
    # تشخیصِ متحمل (مستقل از ترتیب/جمله‌بندی): هر دو واژهٔ «خانم» و «آقا» + علامتِ سؤال.
    _gq = answer or ""
    if (not ctx.get("cards") and not ctx.get("receipt")
            and "خانم" in _gq and "آقا" in _gq and ("؟" in _gq or "?" in _gq)):
        ctx["ask_gender"] = True
    sessions.append(channel, user_id, "user", "[تصویر ساعت] " + (caption or ""))
    sessions.append(channel, user_id, "assistant", answer)
    sessions.add_shown(channel, user_id, [c.get("id") for c in ctx.get("cards", [])])
    _record_metrics(channel, ctx, caption, user_name, user_id, image=True, answer=answer)
    return (answer, ctx)


async def _reply_context_sheet(rc):
    """مشخصاتِ کاملِ محصولی که مشتری به کارتش ریپلای کرده — برای تزریقِ قطعی به مغز.

    rc: {"url"/"name"/"reference"} از کارتِ ریپلای‌شده. محصول را دقیق resolve می‌کند (slug/کدِ رفرنس)
    تا با محصولِ دیگری اشتباه نشود (ریشهٔ باگِ تروساردی→سیتیزن)."""
    try:
        brief = await woo.resolve_product(
            url=(rc.get("url") or ""), name=(rc.get("name") or ""), reference=(rc.get("reference") or ""))
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] resolveِ محصولِ ریپلای ناموفق: {type(e).__name__}: {e}")
        return ""
    if not brief or not brief.get("id"):
        print(f"[assistant] resolve بدون نتیجه: url={rc.get('url')!r} name={rc.get('name')!r}")
        return ""
    try:
        full = await woo.get_product(brief["id"])
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] get_product ناموفق ({brief['id']})؛ از brief استفاده می‌کنم: {type(e).__name__}: {e}")
        full = brief  # به‌جای خطای کامل، با همان خلاصهٔ کارت جواب بده
    parts = [full.get("name", "")]
    if full.get("price_label"):
        parts.append("قیمت: " + full["price_label"])
    if full.get("shipping_time"):
        parts.append("ارسال: " + full["shipping_time"])
    for a in (full.get("attributes") or []):
        nm = (a.get("name") or "").strip()
        opts = a.get("options") or []
        if nm and opts:
            parts.append(f"{nm}: " + "، ".join(str(o) for o in opts))
    sheet = " | ".join(x for x in parts if x)
    if not sheet:
        return ""
    return ("⚡ مشتری به کارتِ یک محصولِ مشخص ریپلای کرده و دربارهٔ **همان** می‌پرسد. "
            f"مشخصاتِ کاملِ همان محصول: {sheet}. فقط دربارهٔ همین محصول جواب بده، "
            "محصولِ دیگری را با آن اشتباه نگیر و کارتِ جدید نشان نده مگر مشتری صریحاً بخواهد.")


async def answer_messages(messages, system_extra="", render_cards_inline=True, reply_context=None, customer=None):
    """پاسخ به یک گفتگوی آماده (فرمت {role, content}) — برای اتصال CRM/sale-brain و کانال‌ها.

    پرسونای محصول‌آگاهِ ما + (اختیاری) دستور سیستمیِ CRM را ترکیب می‌کند و
    با ابزارهای ووکامرس پاسخ می‌سازد. خروجی: (متن، ctx) که ctx ممکن است
    شامل {"cards": [...], "wrist_media": {...}, "handoff": {...}, "order": {...}} باشد.

    render_cards_inline=True: کارت‌ها را به‌صورت متن داخلِ پاسخ می‌پزد (برای کانالِ متن‌محور).
    render_cards_inline=False: فقط مقدمهٔ تمیز را در متن می‌گذارد و کارت‌ها را ساختاریافته در
    ctx['cards'] نگه می‌دارد تا کانال خودش آن‌ها را (به‌صورت عکس/کارت) رندر کند.
    """
    system = persona.system_prompt() + _ab_extra((customer or {}).get("channel"), (customer or {}).get("id"))
    extra = (system_extra or "").strip()
    if reply_context:  # مشتری به کارتِ یک محصول ریپلای کرده → مشخصاتِ همان را قطعی تزریق کن
        sheet = await _reply_context_sheet(reply_context)
        if sheet:
            extra = (extra + "\n\n" + sheet).strip() if extra else sheet
    if extra:
        system = system + "\n\n" + extra

    # سلامِ هوشمندِ ۶ ساعته (همهٔ کانال‌ها)
    if customer and customer.get("id"):
        _gh = _greeting_hint(str(customer.get("channel") or "ch"), str(customer.get("id")))
        if _gh:
            system = system + "\n\n" + _gh

    convo = [{"role": "system", "content": system}]
    if customer:   # سابقهٔ خریدِ مشتری (برای واتساپ، آیدی همان شماره است)
        _ph = customer.get("phone") or (customer.get("id") if customer.get("channel") == "whatsapp" else None)
        if _ph:
            try:
                import crm_index
                _hh = await crm_index.history_hint(customer.get("channel"), customer.get("id"), _ph)
                if _hh:
                    convo.append({"role": "system", "content": _hh})
            except Exception:  # noqa: BLE001
                pass
    for m in messages or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            convo.append({"role": role, "content": content})
    if len(convo) == 1:  # هیچ پیام واقعی‌ای نبود
        return ("", {})

    ctx: dict = {}
    if reply_context and (reply_context.get("name") or "").strip():
        ctx["reacted_product"] = reply_context["name"].strip()   # ری‌اکشنِ کاربر به کارتِ این محصول (برای آمارِ تقاضا)
    # ردگیریِ کارت‌های نشان‌داده‌شده per (channel,user) → عدمِ‌تکرار + صفحه‌بندیِ ۷→۵→۳ روی کانال‌ها
    _ck = (str(customer.get("channel") or "ch"), str(customer.get("id"))) if (customer and customer.get("id")) else None
    if _ck:
        ctx["shown_ids"] = list(sessions.shown_ids(_ck[0], _ck[1]))
    try:
        text = await llm.chat(convo, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] خطا در answer_messages: {type(e).__name__}: {e}")
        text = ""
    text = textfmt.clean_for_chat(text)
    cards = ctx.get("cards") or []
    if cards and render_cards_inline:  # کانالِ متن‌محور: متن را پاک و کارت‌ها را به‌صورت متن ضمیمه کن
        intro = textfmt.strip_product_lines(text) or "چند گزینهٔ خوب و مناسب براتون پیدا کردم 🌟 در ادامه ببینید:"
        text = (intro + "\n\n" + _cards_as_text(cards)).strip()
    elif cards:  # کانال خودش کارت‌ها را رندر می‌کند → فقط مقدمهٔ تمیزِ گفتگویی
        text = textfmt.strip_product_lines(text) or "چند گزینهٔ خوب و مناسب براتون پیدا کردم 🌟 در ادامه ببینید:"
    wm = ctx.get("wrist_media")
    if wm and wm.get("ids"):  # لینکِ پستِ چنلِ مدیای روی‌مچ — برای همهٔ کانال‌ها (نه فقط چت‌سایت)
        links = "\n".join(f"https://t.me/{wm['channel']}/{i}" for i in wm["ids"][:4])
        text = (text + "\n\n🎥 عکس و ویدئوی روی مچ‌دستِ همین ساعت:\n" + links).strip()
    if _ck and ctx.get("cards"):  # ثبتِ کارت‌های نشان‌داده‌شده تا دفعهٔ بعد تکرار نشوند
        sessions.add_shown(_ck[0], _ck[1], [c.get("id") for c in ctx["cards"] if c.get("id")])
    _record_metrics((customer or {}).get("channel"), ctx, _last_user_text(messages), (customer or {}).get("name"), (customer or {}).get("id"), answer=text)
    return (text, ctx)


async def polish_staff_reply(staff_text, question=""):
    """پاسخِ خامِ همکار را به پیامِ گرم و حرفه‌ایِ مشتری‌پسند بازنویسی می‌کند (بدونِ افزودنِ اطلاعاتِ نادرست)."""
    staff_text = (staff_text or "").strip()
    if not staff_text:
        return ""
    sys = ("تو مشاورِ فروشِ گالری جواهریانی. همکارت پاسخِ کوتاهی به سؤالِ یک مشتری داده. این پاسخ را به یک پیامِ "
           "گرم، مؤدبانه و فارسیِ روان برای همان مشتری بازنویسی کن. هیچ اطلاعاتِ تازه یا قیمت یا ادعای نادرستی "
           "اضافه نکن — فقط همین پاسخ را زیبا و حرفه‌ای و کوتاه بیان کن. اگر پاسخِ همکار لینک/عدد دارد عیناً نگه دار.")
    user = (f"سؤالِ مشتری: {question}\n" if question else "") + f"پاسخِ همکار: {staff_text}"
    try:
        out = await llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], {})
        return textfmt.clean_for_chat(out) or staff_text
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] polish_staff_reply ناموفق: {type(e).__name__}: {e}")
        return staff_text


async def generate_followup(messages, name="", channel=""):
    """پیامِ پیگیریِ کوتاه و مشاوره‌محور بر اساسِ همین گفتگوی اخیر می‌سازد (به‌جای متنِ ثابت). خالی برمی‌گرداند اگر نشد."""
    recent = []
    for m in (messages or [])[-12:]:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        content = (m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or ""
        content = str(content).strip()
        if not content:
            continue
        who = "مشتری" if role == "user" else "ما"
        recent.append(f"{who}: {content[:300]}")
    convo = "\n".join(recent[-10:]).strip()
    sys = (
        "تو مشاورِ فروشِ گالری جواهریان (فروشگاهِ تخصصیِ ساعت) هستی. یک گفتگوی اخیر با مشتری مدتی است ساکت مانده. "
        "یک «پیامِ پیگیریِ» کوتاه بنویس که: به‌طور طبیعی و نامحسوس به موضوعِ همین گفتگوی اخیر اشاره کند (نه متنِ عمومیِ ثابت و کلیشه‌ای)؛ "
        "مشتری را بدونِ فشار و بدونِ حسِ مزاحمت، دوباره به ادامهٔ گفتگو و مشاوره دعوت کند؛ گرم، محترمانه و کاملاً فارسیِ روان و بدونِ کلمهٔ لاتین باشد؛ "
        "کوتاه (۱ تا ۳ جمله) با ایموجیِ کم و به‌جا. اگر مشتری دنبالِ مدل/بودجه/سلیقهٔ خاصی بود همان را ظریف یادآوری کن. "
        "اگر نامِ مشتری داده شد، محترمانه با نامش شروع کن. فقط خودِ متنِ پیام را برگردان و هیچ توضیحِ اضافه ننویس."
    )
    user = ((f"نامِ مشتری: {name}\n" if name else "") + (f"کانال: {channel}\n" if channel else "")
            + "گفتگوی اخیر (قدیمی به جدید):\n"
            + (convo or "(تاریخچهٔ گفتگو در دسترس نیست؛ یک پیگیریِ گرم و کوتاهِ مشاوره‌محور برای مشتریِ گالری بنویس.)"))
    try:
        resp = await llm._create([{"role": "system", "content": sys}, {"role": "user", "content": user}], with_tools=False)
        out = (resp.choices[0].message.content or "").strip()
        return textfmt.clean_for_chat(out) or out
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] generate_followup ناموفق: {type(e).__name__}: {e}")
        return ""


async def answer_image(image_data_url, caption="", messages=None, render_cards_inline=True, customer=None):
    """تشخیصِ عکسِ ساعت (بدونِ حالت/session) برای همهٔ کانال‌ها — مثلِ answer_messages ولی با تصویر.

    خروجی: (text, ctx) که ctx['cards'] محصولاتِ پیشنهادی را دارد."""
    user_text = (caption or "").strip()
    user_text = (user_text + "\n\n").strip() + (
        "\nابتدا تصویر را با دقت بررسی کن و فقط وقتی **۹۰٪+ مطمئنی** اقدام کن:\n"
        "• اگر **فیش/رسیدِ پرداختِ بانکی** است: جستجوی ساعت نکن؛ بگو «رسیدِ پرداختتون دریافت شد ✅ "
        "همکاران بررسی می‌کنن و نتیجهٔ تأیید رو خدمتتون اعلام می‌کنیم 🙏»، و اگر مبلغ/تاریخ/شمارهٔ پیگیری خواناست کوتاه بازگو کن.\n"
        "• اگر **ساعت** است و با اطمینان تشخیصش دادی: با search_watches همان یا مشابه‌هایش را پیدا کن، "
        "بعد حتماً با show_products به‌صورت کارت نشان بده.\n"
        "• اگر **ساعت** است ولی **مطمئن نیستی زنانه است یا مردانه** (مثلاً قابِ متوسط یا مدلی بینِ زنانه و مردانه): "
        "هیچ ساعتی نشان نده و حدس نزن؛ رنگ/استایلی که از تصویر فهمیدی را کوتاه بگو و حتماً این عبارت را در سؤالت بیاور "
        "«این ساعت رو برای خانم می‌خواید یا آقا؟»، بعد از جواب با همان مشخصات + جنسیت جستجو کن (این یک سؤالِ کوتاه است، نه ارجاع به همکاران).\n"
        "• اگر تصویر **اصلاً ساعت نیست** (و رسیدِ پرداخت هم نیست) — مثلاً عکسِ شخص، مکان، یا شیءِ نامرتبط: "
        "به همکاران ارجاع نده و محصول نشان نده؛ در عوض مؤدبانه و گرم مشتری را وارد گفتگو کن "
        "(بپرس دنبالِ چه ساعتی هستند یا چطور می‌توانی کمکشان کنی). "
        "**حتماً پاسخت را دقیقاً با نشانهٔ ‹NOWATCH› شروع کن** — این نشانه فقط برای سیستم است و حذف می‌شود.\n"
        "• اگر **ساعت است ولی مطمئن نیستی** (برند/مدل واضح نیست، تصویر مبهم است، یا مطمئن نیستی موجود داریم): "
        "**محصولِ حدسی نشان نده**؛ فقط بگو «عکستون رو دیدم، برای دقت از همکارانم می‌پرسم و جوابتون رو می‌فرستم 🙏» "
        "(تا به گروهِ همکاران ارجاع شود). هرگز محصول یا برندِ اشتباه به مشتری نسبت نده.")
    user_text += _IMAGE_TEXT_HINT
    _sys = persona.system_prompt() + _ab_extra((customer or {}).get("channel"), (customer or {}).get("id"))
    if customer and customer.get("id"):
        _gh = _greeting_hint(str(customer.get("channel") or "ch"), str(customer.get("id")))
        if _gh:
            _sys = _sys + "\n\n" + _gh
    convo = [{"role": "system", "content": _sys}]
    for m in (messages or []):
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            convo.append({"role": role, "content": content})
    convo.append({"role": "user", "content": [
        {"type": "text", "text": user_text},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]})
    ctx: dict = {}
    # عدمِ‌تکرارِ کارت per (channel,user) مثلِ مسیرِ متن (اگر کانال customer داد)
    _ck = (str(customer.get("channel") or "ch"), str(customer.get("id"))) if (customer and customer.get("id")) else None
    if _ck:
        ctx["shown_ids"] = list(sessions.shown_ids(_ck[0], _ck[1]))
    try:
        text = await llm.chat(convo, ctx, model=modelcfg.vision_model())  # مدلِ عکس (جدا از مدلِ چت)
    except Exception as e:  # noqa: BLE001
        print(f"[assistant] خطا در answer_image: {type(e).__name__}: {e}")
        text = ""
    text = textfmt.clean_for_chat(text)
    if "‹NOWATCH›" in text[:40] or "<NOWATCH>" in text[:40] or "NOWATCH" in text[:20]:
        ctx["not_watch"] = True   # عکسِ غیرِ ساعت → مشتری را وارد گفتگو کن، به همکاران ارجاع نده
        text = text.replace("‹NOWATCH›", "").replace("<NOWATCH>", "").replace("NOWATCH", "").strip()
    cards = ctx.get("cards") or []
    _intro = "چند ساعتِ نزدیک به تصویری که فرستادید پیدا کردم 🌟 ببینید:"
    if cards and render_cards_inline:
        intro = textfmt.strip_product_lines(text) or _intro
        text = (intro + "\n\n" + _cards_as_text(cards)).strip()
    elif cards:
        text = textfmt.strip_product_lines(text) or _intro
    # فقط سؤالِ جنسیت پرسیده شده (نه کارت/رسید) → فلگ بزن تا ساختار ارجاع‌به‌همکاران رخ ندهد (تشخیصِ متحمل)
    _gq = text or ""
    if (not cards and not ctx.get("receipt")
            and "خانم" in _gq and "آقا" in _gq and ("؟" in _gq or "?" in _gq)):
        ctx["ask_gender"] = True
    if _ck and cards:  # ثبتِ کارت‌های تصویری در همان مخزنِ shown_ids (عدمِ‌تکرار با مسیرِ متن)
        sessions.add_shown(_ck[0], _ck[1], [c.get("id") for c in cards if c.get("id")])
    _record_metrics((customer or {}).get("channel"), ctx, caption, (customer or {}).get("name"), (customer or {}).get("id"), image=True, answer=text)
    return (text, ctx)


def _cards_as_text(cards):
    out = []
    for c in cards:
        block = ["⌚ " + (c.get("name", "") or "")]
        if c.get("on_sale") and c.get("sale_price_label"):
            reg = c.get("regular_price_label", "")
            block.append(f"🔖 {c['sale_price_label']}" + (f" (قبلاً {reg})" if reg else "") + " ✨")
        elif c.get("price_label"):
            block.append("💰 " + c["price_label"])
        ship = c.get("shipping_time", "")   # «موجود در فروشگاه» نمایش داده نشود؛ روی «ارسال فوری» مانور بده
        if ship == "ارسال فوری":
            block.append("⚡ ارسال فوری")
        elif ship:
            block.append("🚚 " + ship)
        if c.get("url"):
            block.append("🔗 " + c["url"])
        out.append("\n".join(block))
    return "\n\n".join(out)
