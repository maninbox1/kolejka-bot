# kolejka_bot

Telegram bot that, on request, shows the passenger-car queue at **Брузги** and **Берестовица**
(data from https://mon.declarant.by/#/zone).

The site only returns the vehicle list to its own page, so the bot opens the zone page in a
headless browser (Playwright) and reads the data the page loads. One page load per request,
cached for 60 s.

## Run

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # headless browser used to load the page
export TELEGRAM_BOT_TOKEN=123456:ABC...   # from @BotFather
.venv/bin/python bot.py
```

Commands: `/start` (buttons), `/bruzgi`, `/berestovitsa`, `/all`, `/alarm`, `/alarms`.

## Alarm

⏰ Set alarm → border → until what time (Minsk) → interval (5–60 min). The bot checks right away,
then every N minutes until that time, and sends a push notification whenever there are more than
30 cars **or** the estimated wait is ≥ 1 hour (`MAX_CARS` / `MAX_WAIT` in `alarm.py`).
One alarm per border; 🔔 My alarms shows the last check and lets you stop it.

Alarms live in memory: a restart/redeploy clears them. On Render the bot pings its own URL
every 10 min while an alarm is active, so the free instance doesn't fall asleep.

## What it shows

- cars waiting in the queue now (+ priority queue)
- cars sent to the checkpoint in the last hour / 24h (same numbers as the site)
- when the first car still in the queue registered, and when the latest one registered
- when the last car was called, and how long recently called cars waited
- **wait estimate if you register now** = cars ahead ÷ throughput (average of last-hour
  rate and 24h rate), with a range that also takes into account how long the front car
  has waited and the actual waits of recently called cars

## Tests

```sh
.venv/bin/python -m pytest -q
```
