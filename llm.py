"""اتصال به جی‌پی‌تی و اجرای حلقه‌ی فراخوانی ابزار (function calling)."""
from __future__ import annotations

from openai import AsyncOpenAI

import config
import modelcfg
import tools

_client = None


def client():
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


async def _create(messages, with_tools=True, model=None):
    model = (model or modelcfg.chat_model())
    kwargs = {"model": model, "messages": messages}
    # مدل‌های استدلالیِ GPT-5/o-سری فقط temperatureِ پیش‌فرض را می‌پذیرند → temperature را نفرست؛
    # به‌جایش (در صورتِ تنظیم) reasoning_effort بده. مدل‌های قدیمی‌تر (gpt-4o…) temperature می‌گیرند.
    if model.startswith(("gpt-5", "o1", "o3", "o4")):
        eff = (getattr(config, "OPENAI_REASONING_EFFORT", "") or "").strip().lower()
        # ⚠️ gpt-5.5 در chat.completions «ابزار + reasoning_effort» را با هم نمی‌پذیرد (خطای 400).
        # چون مسیرِ اصلی همیشه ابزار دارد، effort را فقط وقتی می‌فرستیم که ابزار وصل نباشد.
        if eff and eff != "none" and not with_tools:
            kwargs["reasoning_effort"] = eff
        # بودجهٔ خروجی لازم است وگرنه reasoning کلِ بودجه را می‌خورد و متن خالی برمی‌گردد
        kwargs["max_completion_tokens"] = int(getattr(config, "OPENAI_MAX_COMPLETION_TOKENS", 4000))
    else:
        kwargs["temperature"] = config.OPENAI_TEMPERATURE
    if with_tools:
        kwargs["tools"] = tools.SCHEMAS
        kwargs["tool_choice"] = "auto"
    return await client().chat.completions.create(**kwargs)


async def chat(messages, ctx, model=None):
    """گفتگو با مدل به‌همراه حلقه‌ی ابزار. ورودی فهرست پیام‌ها (شامل system) است.

    model: اگر داده شود (مثلاً مدلِ عکس در مسیرِ vision) به‌جای مدلِ چتِ پیش‌فرض استفاده می‌شود.
    خروجی: متن نهایی پاسخ. ctx برای سیگنال‌هایی مثل ارجاع به اپراتور پر می‌شود.
    """
    msgs = list(messages)
    for _ in range(max(1, config.MAX_TOOL_ROUNDS)):
        resp = await _create(msgs, with_tools=True, model=model)
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            return (msg.content or "").strip()

        # پیام assistant با درخواست ابزار را عیناً به تاریخچه‌ی موقت اضافه کن
        msgs.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in calls
            ],
        })
        # هر ابزار را اجرا و نتیجه را به‌عنوان نقش tool برگردان
        for c in calls:
            result = await tools.dispatch(c.function.name, c.function.arguments, ctx)
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": result})

    # اگر بعد از سقف دورها هنوز ابزار می‌خواست، یک پاسخ نهاییِ بدون ابزار بگیر
    resp = await _create(msgs, with_tools=False, model=model)
    return (resp.choices[0].message.content or "").strip()


async def complete(messages, model=None, max_tokens=8000, effort=None):
    """یک فراخوانیِ ساده و بدونِ ابزار — برای تحلیل/گزارشِ مدیریتی و پاسخ به سوالِ مدیر.

    بودجهٔ خروجی بزرگ‌تر (پیش‌فرض ۸۰۰۰) چون گزارش مفصل است. متنِ خام برمی‌گرداند.
    """
    m = (model or modelcfg.analysis_model())
    kwargs = {"model": m, "messages": messages}
    if m.startswith(("gpt-5", "o1", "o3", "o4")):
        eff = (effort or getattr(config, "OPENAI_REASONING_EFFORT", "") or "").strip().lower()
        # بدونِ ابزار → می‌توان reasoning_effort فرستاد (محدودیتِ ۴۰۰ فقط وقتی ابزار هم باشد)
        if eff and eff != "none":
            kwargs["reasoning_effort"] = eff
        kwargs["max_completion_tokens"] = int(max_tokens)
    else:
        kwargs["temperature"] = config.OPENAI_TEMPERATURE
    resp = await client().chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


async def transcribe(audio_bytes, filename="voice.ogg"):
    """رونویسی پیام صوتی به متن (Whisper)."""
    resp = await client().audio.transcriptions.create(
        model="whisper-1",
        file=(filename, audio_bytes),
    )
    return (getattr(resp, "text", "") or "").strip()
