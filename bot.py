"""Telegram bot: on request, shows the passenger-car queue at Bruzgi / Berestovitsa,
and can run an alarm that checks the queue periodically and notifies when it gets long."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timedelta

import httpx
from telegram import BotCommand, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from alarm import INTERVALS_MIN, MAX_CARS, MAX_WAIT, alarm_reasons, hour_choices, parse_until
from border import CHECKPOINTS, MINSK, Summary, fmt_td, render, summarise
from fetcher import BrowserFetcher

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("kolejka")

CACHE_TTL = 120  # seconds — reuse a result for 2 min (loading is slow on small hosts)
LOADING = "⏳ Loading… this can take 1–3 minutes."
_cache: dict[str, tuple[float, Summary]] = {}

PUBLIC_URL = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
KEEPALIVE_S = 10 * 60  # Render free sleeps after 15 min without inbound traffic

MAIN_KB = InlineKeyboardMarkup([
    [Btn(cp["name"], callback_data=f"q:{slug}") for slug, cp in CHECKPOINTS.items()],
    [Btn("Both", callback_data="q:all")],
    [Btn("⏰ Set alarm", callback_data="al:new"), Btn("🔔 My alarms", callback_data="al:list")],
])


# ---------- data ----------

async def get_summary(slug: str, context: ContextTypes.DEFAULT_TYPE) -> Summary:
    cached = _cache.get(slug)
    if cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]
    fetcher: BrowserFetcher = context.bot_data["fetcher"]
    queue, stats = await fetcher.fetch(slug)
    s = summarise(CHECKPOINTS[slug]["name"], queue, stats)
    _cache[slug] = (time.monotonic(), s)
    return s


async def report(slug: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        return render(await get_summary(slug, context))
    except Exception as e:  # network / format problems — tell the user instead of crashing
        log.exception("fetch failed for %s", slug)
        return f"⚠️ {CHECKPOINTS[slug]['name']}: couldn't load data ({type(e).__name__}: {str(e)[:120]})."


async def build(slugs: list[str], context: ContextTypes.DEFAULT_TYPE) -> str:
    return "\n\n".join([await report(s, context) for s in slugs])


# ---------- queue lookups ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Which border crossing?\n\nCommands: /bruzgi, /berestovitsa, /all, /alarm, /alarms",
        reply_markup=MAIN_KB,
    )


def command(slugs: list[str]):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = await update.message.reply_text(LOADING)
        text = await build(slugs, context)
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)
    return handler


async def on_queue_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer("Loading…")
    what = q.data.split(":", 1)[-1]  # also accepts legacy buttons without the "q:" prefix
    slugs = list(CHECKPOINTS) if what == "all" else [what]
    msg = await q.message.reply_text(LOADING)
    text = await build(slugs, context)
    await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)


# ---------- alarm setup (border -> until -> interval) ----------

def _job_name(chat_id: int, slug: str) -> str:
    return f"alarm:{chat_id}:{slug}"


def _until_kb() -> InlineKeyboardMarkup:
    hours = hour_choices(datetime.now(MINSK), 12)
    rows = [[Btn(h, callback_data=f"al:u:{h.replace(':', '')}") for h in hours[i:i + 4]]
            for i in range(0, len(hours), 4)]
    rows.append([Btn("✖ Cancel", callback_data="al:cancel")])
    return InlineKeyboardMarkup(rows)


async def alarm_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("alarm_draft", None)
    kb = InlineKeyboardMarkup([
        [Btn(cp["name"], callback_data=f"al:b:{slug}") for slug, cp in CHECKPOINTS.items()],
        [Btn("✖ Cancel", callback_data="al:cancel")],
    ])
    text = "⏰ <b>New alarm</b>\nStep 1/3 — which border?"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def alarm_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    action = parts[1]
    draft = context.user_data.setdefault("alarm_draft", {})

    if action == "new":
        await alarm_new(update, context)
    elif action == "cancel":
        context.user_data.pop("alarm_draft", None)
        await q.edit_message_text("Alarm setup cancelled.", reply_markup=MAIN_KB)
    elif action == "menu":
        await q.edit_message_text("Which border crossing?", reply_markup=MAIN_KB)
    elif action == "list":
        await show_alarms(update, context)
    elif action == "x":  # stop an alarm
        slug = parts[2]
        for job in context.job_queue.get_jobs_by_name(_job_name(q.message.chat_id, slug)):
            job.schedule_removal()
        await q.edit_message_text(f"🔕 Alarm for {CHECKPOINTS[slug]['name']} stopped.",
                                  reply_markup=MAIN_KB)
    elif action == "b":
        draft.clear()
        draft["slug"] = parts[2]
        draft["await_until"] = True
        await q.edit_message_text(
            f"⏰ <b>{CHECKPOINTS[parts[2]]['name']}</b>\n"
            "Step 2/3 — check <b>until</b> what time (Minsk)?\n"
            "Pick one or type it, e.g. <code>04:00</code>",
            parse_mode=ParseMode.HTML, reply_markup=_until_kb())
    elif action == "u":
        hhmm = parts[2]
        await _set_until(update, context, f"{hhmm[:2]}:{hhmm[2:]}")
    elif action == "i":
        if "slug" not in draft or "until" not in draft:
            await q.edit_message_text("Setup expired, start again.", reply_markup=MAIN_KB)
            return
        await _create_alarm(update, context, draft, int(parts[2]))


async def _set_until(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    draft = context.user_data.get("alarm_draft") or {}
    if "slug" not in draft:
        return
    until = parse_until(text, datetime.now(MINSK))
    if until is None:
        await update.effective_message.reply_text("Didn't get that time. Send it like 04:00.")
        return
    draft["until"] = until.isoformat()
    draft.pop("await_until", None)
    kb = InlineKeyboardMarkup([
        [Btn(f"{m} min", callback_data=f"al:i:{m}") for m in INTERVALS_MIN],
        [Btn("✖ Cancel", callback_data="al:cancel")],
    ])
    text = (f"⏰ <b>{CHECKPOINTS[draft['slug']]['name']}</b> until <b>{until:%d.%m %H:%M}</b>\n"
            "Step 3/3 — check how often?")
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft = context.user_data.get("alarm_draft") or {}
    if draft.get("await_until"):
        await _set_until(update, context, update.message.text)
    else:
        await update.message.reply_text("Choose an option:", reply_markup=MAIN_KB)


async def _create_alarm(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict,
                        interval_min: int) -> None:
    chat_id = update.effective_chat.id
    slug = draft["slug"]
    until = datetime.fromisoformat(draft["until"])
    context.user_data.pop("alarm_draft", None)
    if until <= datetime.now(MINSK):
        await update.callback_query.edit_message_text("That time has already passed.",
                                                      reply_markup=MAIN_KB)
        return

    name = _job_name(chat_id, slug)
    for job in context.job_queue.get_jobs_by_name(name):  # one alarm per border — replace
        job.schedule_removal()
    context.job_queue.run_repeating(
        alarm_tick, interval=interval_min * 60, first=1, name=name, chat_id=chat_id,
        data={"slug": slug, "until": until, "interval": interval_min, "last": None})
    _ensure_keepalive(context)

    await update.callback_query.edit_message_text(
        f"✅ Alarm set: <b>{CHECKPOINTS[slug]['name']}</b>\n"
        f"Checking every {interval_min} min until {until:%d.%m %H:%M} (Minsk).\n"
        f"I'll notify you when there are more than {MAX_CARS} cars "
        f"or the wait is {fmt_td(MAX_WAIT)} or longer.",
        parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)


# ---------- alarm runtime ----------

async def alarm_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    d = job.data
    slug, name = d["slug"], CHECKPOINTS[d["slug"]]["name"]
    now = datetime.now(MINSK)

    try:
        s = await get_summary(slug, context)
        reasons = alarm_reasons(s)
        d["last"] = (f"{now:%H:%M}: {s.in_queue} cars, wait ~{fmt_td(s.estimate)}"
                     + (" 🚨" if reasons else " ✅"))
        d.pop("failed", None)
        if reasons:
            await context.bot.send_message(
                job.chat_id,
                f"🚨 <b>{name}: queue is long</b>\n" + "\n".join(f"• {r}" for r in reasons)
                + f"\n\n{render(s)}",
                parse_mode=ParseMode.HTML)  # normal (sound) notification
    except Exception as e:
        log.exception("alarm check failed for %s", slug)
        d["last"] = f"{now:%H:%M}: check failed ({type(e).__name__})"
        if not d.get("failed"):  # tell once, quietly, then keep trying
            d["failed"] = True
            await context.bot.send_message(job.chat_id, f"⚠️ {name}: alarm check failed, will retry.",
                                           disable_notification=True)

    if now + timedelta(minutes=d["interval"]) > d["until"]:
        job.schedule_removal()
        await context.bot.send_message(job.chat_id, f"⏰ Alarm for {name} finished.",
                                       disable_notification=True, reply_markup=MAIN_KB)


def _alarm_jobs(context: ContextTypes.DEFAULT_TYPE, chat_id: int | None = None):
    return [j for j in context.job_queue.jobs()
            if j.name and j.name.startswith("alarm:") and not j.removed
            and (chat_id is None or j.chat_id == chat_id)]


async def show_alarms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    jobs = _alarm_jobs(context, update.effective_chat.id)
    if not jobs:
        text, kb = "No active alarms.", MAIN_KB
    else:
        lines, buttons = ["🔔 <b>Active alarms</b>"], []
        for j in jobs:
            d = j.data
            n = CHECKPOINTS[d["slug"]]["name"]
            lines.append(f"\n<b>{n}</b> — every {d['interval']} min until {d['until']:%d.%m %H:%M}"
                         f"\nLast check: {d['last'] or 'pending'}")
            buttons.append([Btn(f"🔕 Stop {n}", callback_data=f"al:x:{d['slug']}")])
        buttons.append([Btn("⬅ Menu", callback_data="al:menu")])
        text, kb = "\n".join(lines), InlineKeyboardMarkup(buttons)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


def _ensure_keepalive(context: ContextTypes.DEFAULT_TYPE) -> None:
    """On Render free, ping our own public URL while alarms run so the service doesn't sleep."""
    if PUBLIC_URL and not context.job_queue.get_jobs_by_name("keepalive"):
        context.job_queue.run_repeating(keepalive, interval=KEEPALIVE_S, first=KEEPALIVE_S,
                                        name="keepalive")


async def keepalive(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _alarm_jobs(context):
        context.job.schedule_removal()
        return
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.get(PUBLIC_URL)
    except Exception:
        log.warning("keepalive ping failed")


# ---------- app ----------

COMMANDS = [
    BotCommand("start", "Main menu"),
    BotCommand("bruzgi", "Queue at Брузги"),
    BotCommand("berestovitsa", "Queue at Берестовица"),
    BotCommand("all", "Both crossings"),
    BotCommand("alarm", "Set an alarm"),
    BotCommand("alarms", "My alarms"),
]


async def post_init(app: Application) -> None:
    app.bot_data["fetcher"] = BrowserFetcher()  # browser starts on first request
    await app.bot.set_my_commands(COMMANDS)  # fills Telegram's "Menu" button next to the input


async def post_shutdown(app: Application) -> None:
    await app.bot_data["fetcher"].stop()


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (get one from @BotFather).")
    app = (Application.builder().token(token)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("bruzgi", command(["bruzgi"])))
    app.add_handler(CommandHandler("berestovitsa", command(["berestovitsa"])))
    app.add_handler(CommandHandler("all", command(list(CHECKPOINTS))))
    app.add_handler(CommandHandler("alarm", alarm_new))
    app.add_handler(CommandHandler("alarms", show_alarms))
    app.add_handler(CallbackQueryHandler(on_queue_button, pattern=r"^(q:|bruzgi$|berestovitsa$|all$)"))
    app.add_handler(CallbackQueryHandler(alarm_button, pattern=r"^al:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # On Render (or any host with a public HTTPS URL) use a webhook: Telegram POSTs each update to
    # us, which also wakes a sleeping free-tier service. Locally, fall back to polling.
    if PUBLIC_URL:
        secret = hashlib.sha256(token.encode()).hexdigest()[:32]  # Telegram echoes it; rejects fakes
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", "10000")),
            url_path="telegram",
            webhook_url=f"{PUBLIC_URL.rstrip('/')}/telegram",
            secret_token=secret,
            drop_pending_updates=True,
        )
    else:
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
