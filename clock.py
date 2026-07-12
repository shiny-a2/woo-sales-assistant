"""ساعت واقعی، مستقل از ساعتِ احتمالاً‌نادرستِ سرور.

آفستِ بین ساعت سرور و زمان واقعی را از هدر Date یک سرور معتبر می‌گیرد.
تهران بدون DST همیشه UTC+۳:۳۰ است. (هم‌نسخه با tg-outreach/clock.py)
"""
from __future__ import annotations

import asyncio
import datetime
import email.utils

import requests

_TEHRAN = datetime.timedelta(hours=3, minutes=30)
_offset = datetime.timedelta(0)  # real_utc - server_utcnow()
_HOSTS = ("https://www.google.com", "https://www.cloudflare.com", "https://api.telegram.org")


def _fetch_real_utc():
    last_err = None
    for host in _HOSTS:
        try:
            resp = requests.head(host, timeout=8)
            date_hdr = resp.headers.get("Date")
            if date_hdr:
                return email.utils.parsedate_to_datetime(date_hdr).replace(tzinfo=None)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err or RuntimeError("no time source")


def refresh_sync():
    global _offset
    try:
        _offset = _fetch_real_utc() - datetime.datetime.utcnow()
    except Exception:
        pass


async def refresh():
    global _offset
    try:
        real = await asyncio.to_thread(_fetch_real_utc)
        _offset = real - datetime.datetime.utcnow()
        print(f"[clock] آفستِ ساعتِ سرور: {_offset.total_seconds() / 3600:+.2f} ساعت")
    except Exception as e:  # noqa: BLE001
        print(f"[clock] همگام‌سازی زمان ناموفق بود: {e}")


def utcnow():
    return datetime.datetime.utcnow() + _offset


def tehran_now():
    return utcnow() + _TEHRAN


def _to_jalali(dt=None):
    """(jy, jm, jd) شمسی از یک datetimeِ میلادی."""
    d = dt or tehran_now()
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy - 1600
    days = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    if gm > 2 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        days += 1
    jy = 979 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    jm = 1 + (days // 31 if days < 186 else 6 + (days - 186) // 30)
    jd = 1 + (days % 31 if days < 186 else (days - 186) % 30)
    return jy, jm, jd


def jalali_str(dt=None):
    """تاریخِ شمسیِ کامل «۱۴۰۴/۰۴/۱۹ ۲۱:۳۰» از میلادی (پیش‌فرض: اکنونِ تهران)."""
    d = dt or tehran_now()
    jy, jm, jd = _to_jalali(d)
    return f"{jy:04d}/{jm:02d}/{jd:02d} {d.strftime('%H:%M')}"


def jalali_date(dt=None):
    """فقط تاریخِ شمسی «۱۴۰۴/۰۴/۱۹» (بدونِ ساعت)."""
    jy, jm, jd = _to_jalali(dt)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def month_start(dt=None):
    """datetimeِ میلادیِ «اولِ ماهِ شمسیِ جاری» ساعت ۰۰:۰۰ (تهران).
    ترفند: امروز روزِ jdاُمِ ماهِ شمسی است، پس اولِ ماه (jd-1) روز پیش بوده — بدونِ نیاز به تبدیلِ کاملِ شمسی→میلادی."""
    import datetime as _dt
    d = dt or tehran_now()
    _, _, jd = _to_jalali(d)
    start = d - _dt.timedelta(days=jd - 1)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)
