"""موتورِ نرمال‌سازیِ نامِ فارسی + تفکیکِ نام/نام‌خانوادگی + تشخیصِ نامِ «غلط/عمومی».

روی همهٔ کانال‌ها اعمال می‌شود تا کانتکت‌ها نام و نام‌خانوادگیِ درستِ فارسی داشته باشند و
نام‌های غلط (مثلِ «مشتری»، شماره، یا لاتینِ نامفهوم) با نامِ ثبتیِ درستِ خودِ شخص جایگزین شوند.

اولویتِ نام (طبقِ تصمیمِ مدیر): فاکتور › CRM › نامِ نمایشیِ ثبتیِ تلگرام/واتساپ (نرمالِ فارسی).
"""
from __future__ import annotations

import re

# ی/ک عربی → فارسی، حذفِ اعرابِ عربی، یکسان‌سازیِ فاصله‌ها
_AR2FA = {
    "ي": "ی", "ى": "ی",   # ي , ى → ی
    "ك": "ک",                        # ك → ک
    "ة": "ه",                        # ة → ه
    "ـ": "",                              # ـ (کشیده)
    "‌": " ", "‏": "", "‎": "",  # نیم‌فاصله/کنترل → فاصله/حذف
}
_DIACRITICS = re.compile(r"[ً-ْٰ]")   # فتحه/کسره/ضمه/تنوین/…
_FA_LETTER = re.compile(r"[ء-ی]")

# القاب/پیشوندهایی که باید از اولِ نام حذف شوند
# فقط القابِ روشن حذف می‌شوند؛ «سید/سیده/کربلایی/مشهدی» چون اغلب جزوِ خودِ نام‌اند نگه داشته می‌شوند
_HONORIFICS = ["جناب آقای", "سرکار خانم", "آقای", "خانم", "جناب", "سرکار", "مهندس", "دکتر",
               "حاج آقا", "حاجی", "حاج", "استاد", "شادروان"]

# واژه‌هایی که یعنی «نامِ واقعی نیست» و باید بازنویسی شوند
_GENERIC = {"مشتری", "مشتری عزیز", "دوست", "دوست عزیز", "کاربر", "ناشناس", "customer", "user",
            "guest", "test", "تست", "نامشخص", "بدون نام", "-", "—", "."}


def _fold(s):
    s = str(s or "")
    for a, b in _AR2FA.items():
        s = s.replace(a, b)
    s = _DIACRITICS.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def _digits_only(s):
    return re.sub(r"\D", "", str(s or ""))


def normalize(name):
    """نامِ خام را به فارسیِ تمیز تبدیل کن (ی/ک فارسی، حذفِ لقب/ایموجی/کاراکترِ اضافه). خالی اگر معنا نداشت."""
    s = _fold(name)
    if not s:
        return ""
    # حذفِ ایموجی و کاراکترهای غیرِ حرف/عدد/فاصلهٔ متعارف
    s = re.sub(r"[^ء-ی٠-٩A-Za-z0-9 \-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    if not s:
        return ""
    # حذفِ لقب از ابتدای نام (تکراری تا همه‌ی پیشوندها برداشته شود)
    changed = True
    while changed:
        changed = False
        for h in _HONORIFICS:
            if s == h:
                return ""
            if s.startswith(h + " "):
                s = s[len(h) + 1:].strip()
                changed = True
    return re.sub(r"\s+", " ", s).strip()


def is_generic(name):
    """آیا این نام «واقعی» نیست (باید با نامِ درست بازنویسی شود)؟"""
    s = normalize(name).lower()
    if not s:
        return True
    if s in _GENERIC:
        return True
    if re.fullmatch(r"[\d\s\-]+", s):          # فقط عدد/شماره
        return True
    if re.fullmatch(r"مشتری[\s\d]*", normalize(name)):   # «مشتری 1234»
        return True
    if len(s) < 2:
        return True
    return False


def is_good_persian(name):
    """نامِ باکیفیتِ فارسی: حرفِ فارسی دارد، عمومی نیست، حداقل دو نویسه."""
    s = normalize(name)
    return bool(s) and not is_generic(s) and bool(_FA_LETTER.search(s))


def split(name):
    """تفکیکِ نام به (نام، نام‌خانوادگی): اولین واژه = نام، بقیه = خانوادگی."""
    s = normalize(name)
    if not s:
        return ("", "")
    parts = s.split(" ")
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], " ".join(parts[1:]))


def best_name(invoice=None, crm=None, display=None):
    """بهترین نام طبقِ اولویت: فاکتور › CRM › نامِ نمایشیِ نرمال‌شده. خالی اگر هیچ‌کدام خوب نبود."""
    for cand in (invoice, crm, display):
        if is_good_persian(cand):
            return normalize(cand)
    # اگر هیچ فارسیِ خوبی نبود، اولین نرمالِ غیرعمومی (حتی لاتین) را بده
    for cand in (invoice, crm, display):
        n = normalize(cand)
        if n and not is_generic(n):
            return n
    return ""


def should_replace(existing, candidate):
    """آیا نامِ موجود را با کاندیدا جایگزین کنیم؟ فقط وقتی موجود غلط/عمومی و کاندیدا خوب باشد."""
    cand = normalize(candidate)
    if not cand or is_generic(cand):
        return False
    if is_generic(existing):
        return True
    # موجود خوب است ولی فارسی نیست و کاندیدا فارسیِ خوب است → ارتقا بده
    if not _FA_LETTER.search(normalize(existing)) and is_good_persian(cand):
        return True
    return False
