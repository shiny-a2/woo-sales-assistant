"""نگاشتِ نامِ مستعار/مخفف/انگلیسیِ برند → نامِ کاملِ فارسیِ برند، برای جستجوی درست.

مثال: کاربر بنویسد «سی‌کی»، «ck»، «calvin klein» → همه به «کلوین کلاین» تبدیل می‌شوند تا
جستجوی ووکامرس محصولِ درست را پیدا کند. قابلِ‌ویرایش: هر برند/نامِ مستعارِ جدید را این‌جا اضافه کن.
"""
from __future__ import annotations

# نامِ کاملِ فارسی (canonical) : [نام‌های مستعار/مخفف/انگلیسی]
_ALIASES = {
    "کلوین کلاین": ["سی کی", "سی‌کی", "ck", "c k", "کلوین", "کالوین کلاین", "کالوین", "calvin klein", "calvinklein", "calvin"],
    "کاسیو": ["casio", "جی شاک", "جی‌شاک", "g shock", "g-shock", "gshock", "جیشاک", "ادیفایس", "edifice", "پروترک", "protrek", "پرو ترک"],
    "سیتیزن": ["citizen", "سیتی زن"],
    "امگا": ["omega", "اُمگا"],
    "سواچ": ["swatch", "سوآچ"],
    "تیسو": ["tissot", "تیست"],
    "سیکو": ["seiko", "سایکو"],
    "اورینت": ["orient", "اوریِنت"],
    "ادوکس": ["edox"],
    "اوماکس": ["omax"],
    "ولدر": ["welder"],
    "فری لوک": ["free look", "freelook", "فری‌لوک"],
    "دنیل کلین": ["daniel klein", "دنیل کلاین", "danielklein", "دنیل‌کلین"],
    "رومانسون": ["romanson"],
    "نیوی فورس": ["naviforce", "navi force", "نیوی‌فورس"],
    "کورن": ["curren", "کارن"],
    "بلنک پین": ["blancpain", "بلنک‌پین", "بلانکپین", "بلانک پین"],
    "پلیس": ["police"],
    "امپریو آرمانی": ["emporio armani", "armani", "آرمانی", "ارمانی", "امپریو ارمانی"],
    "فسیل": ["fossil"],
    "گس": ["guess"],
    "دیزل": ["diesel"],
    "مایکل کورس": ["michael kors", "mk", "مایکل کرس", "مایکل‌کورس"],
    "تگ هویر": ["tag heuer", "tagheuer", "تگ هیر", "تگ‌هویر"],
    "تامی هیلفیگر": ["tommy hilfiger", "tommy", "تامی", "تامی‌هیلفیگر"],
    "ژاک لمن": ["jacques lemans", "ژاک لومن", "ژاک‌لمن"],
    "لیو جو": ["liu jo", "liujo", "لیوجو"],
    "امپریال": ["imperial"],
    "لاکسمی": ["laxmi"],
}


def _norm(s):
    s = (s or "").strip().lower()
    s = s.replace("‌", " ").replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    return " ".join(s.split())


# ایندکسِ معکوس: alias_norm → canonical
_INDEX = {}
for _canon, _al in _ALIASES.items():
    _INDEX[_norm(_canon)] = _canon
    for _a in _al:
        _INDEX[_norm(_a)] = _canon


def canonical(name):
    """نامِ کاملِ فارسیِ برند برای هر نام/مخفف/مستعار. ناشناخته → همان ورودی."""
    if not name:
        return name
    n = _norm(name)
    if n in _INDEX:
        return _INDEX[n]
    padded = " " + n + " "
    for alias_n, canon in _INDEX.items():
        if len(alias_n) >= 2 and (" " + alias_n + " ") in padded:
            return canon
    return name


def normalize_text(text):
    """در یک متنِ آزاد، نام‌های مستعارِ برند را با نامِ کاملِ فارسی جایگزین کن (برای query).

    تک‌گذر و حریصانه (بلندترین نام‌مستعار اول) تا متنِ جایگزین‌شده دوباره اسکن نشود
    (وگرنه «سی کی» → «کلوین کلاین» و بعد «کلوین» دوباره → تکرارِ اشتباه).
    """
    if not text:
        return text
    words = _norm(text).split()
    out, i, changed = [], 0, False
    while i < len(words):
        hit = False
        for span in (3, 2, 1):
            if i + span <= len(words):
                cand = " ".join(words[i:i + span])
                if cand in _INDEX:
                    out.append(_norm(_INDEX[cand]))
                    i += span
                    hit = changed = True
                    break
        if not hit:
            out.append(words[i])
            i += 1
    return " ".join(out) if changed else text


def aliases_hint(limit=10):
    """چند نمونهٔ پرکاربرد برای راهنماییِ پرسونا (تا مدل خودش هم نرمال کند)."""
    ex = []
    for canon, al in list(_ALIASES.items())[:limit]:
        if al:
            ex.append(f"{al[0]}/{al[1] if len(al) > 1 else al[0]} → {canon}")
    return "، ".join(ex)
