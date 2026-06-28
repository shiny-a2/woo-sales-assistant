# دستیار فروش گالری جواهریان (ووکامرس + جی‌پی‌تی)

ربات مشاور و فروش هوشمند که به **ووکامرس** و **جی‌پی‌تی** وصل است و روی دو کانال کار می‌کند:
- **تلگرام** (ربات مستقل)
- **چت سایت** (ویجت قابل‌جاسازی + بک‌اند FastAPI)

دستیار قیمت، موجودی و مشخصات را همیشه به‌صورت زنده از فروشگاه استعلام می‌کند و چیزی از خودش نمی‌سازد.

## امکانات نسخه ۱
- جستجو و پیشنهاد محصول (با بازه‌ی قیمت و دسته‌بندی)
- جزئیات کامل یک محصول
- استعلام وضعیت سفارش (با تأیید شماره تماس)
- ارجاع به اپراتور انسانی (هشدار به ادمین در تلگرام)
- حافظه‌ی گفتگوی هر کاربر

## راه‌اندازی (روی همین سرور ویندوزی)
```powershell
cd c:\A2\woo-sales-assistant
# ساخت venv با virtualenv (نه venv استاندارد — گاچای پروژه‌ی orderbot)
python -m virtualenv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env   # بعد .env را پر کن
.venv\Scripts\python.exe selftest.py   # تست اتصال‌ها
.venv\Scripts\python.exe main.py        # اجرا
```

## تنظیم `.env`
- `TELEGRAM_BOT_TOKEN` از BotFather
- `ADMIN_USER_IDS` آیدی عددی ادمین‌ها (با کاما)
- `WOO_URL`, `WOO_CK`, `WOO_CS` کلید REST ووکامرس (دسترسی فقط‌خواندنی کافی است)
- `OPENAI_API_KEY` و `OPENAI_MODEL` (پیش‌فرض `gpt-4o-mini`)

## افزودن چت به سایت
آدرس بک‌اند را عمومی کن (ساب‌دامین/تونل به پورت `WEB_PORT`)، سپس این خط را قبل از `</body>` سایت بگذار:
```html
<script src="https://CHAT.DOMAIN/embed.js" defer></script>
```
دامنه‌ی سایت را در `WEB_ALLOWED_ORIGINS` فایل `.env` مجاز کن.

## اطلاعات فروشگاه
فایل `store_info.md` را ویرایش کن (ساعت کاری، ارسال، پرداخت، گارانتی). این متن مستقیماً به مغز دستیار داده می‌شود.

## استقرار دائمی
مثل ربات سفارش‌گیر، با **Windows Scheduled Task** (AtStartup، اجرای مستقیم `.venv\Scripts\python.exe -u main.py`، RestartCount بالا). `main.py` خودترمیم است و لاگ تهران روی `data/bot.log` می‌نویسد.

## ساختار
| فایل | نقش |
|------|-----|
| `config.py` | خواندن `.env` |
| `woo.py` | کلاینت ووکامرس (محصول/دسته/سفارش) |
| `persona.py` | شخصیت و پیام سیستمی + `store_info.md` |
| `tools.py` | تعریف ابزارهای جی‌پی‌تی + توزیع فراخوانی |
| `llm.py` | حلقه‌ی جی‌پی‌تی + فراخوانی ابزار |
| `sessions.py` | حافظه‌ی گفتگو |
| `assistant.py` | هسته‌ی پاسخ‌دهی (مستقل از کانال) |
| `telegram_bot.py` | کانال تلگرام |
| `web_server.py` | بک‌اند چت سایت + ویجت |
| `main.py` | نقطه‌ی ورود + supervisor |
