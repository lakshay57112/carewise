# Carewise

Carewise explains a hospital discharge summary in plain language, in the patient's own language, and by voice.

You paste the summary or upload a photo. A small team of agents reads it, checks its own work, builds a medicine timetable, and then you can just talk to it ("agli dawai kaun si hai?"). Speech in and speech out run on Gnani's Prisma and Timbre models.

Built for the Great Indian AI Internship Challenge.

## What the agents do

| Agent | Job |
| --- | --- |
| Planner | With a model key, reads what you said and chooses which agents to run and in what order (up to three steps), e.g. "I missed my morning dose, tell my family" runs Schedule then Caregiver. It only chooses; it never writes the answer |
| Orchestrator | Runs the plan, or routes by keywords when there is no key or the model fails |
| Document | Reads the summary (text or photo) into medicines, diet, follow-up and warning signs |
| Verifier | Re-checks every medicine, dose and date against the original text. Anything unclear is sent back to you to confirm, and the timetable stays locked until you do |
| Schedule | Builds the day's timetable, finds the next dose, logs doses as taken or missed |
| Check-in | Compares what you say ("mujhe bukhar hai") with the warning signs your own doctor wrote. It never diagnoses |
| Caregiver | Writes a short update for the family and carries any alert |
| Reminders | At each dose time, notifies your phone (a notification, and/or a Telegram voice message), even when the page is closed. Tap Taken to tick the dose off |
| Hand-off | Passes the hospital details to the Insurithm AI claim agent when you mention an insurance problem |
| Explainer | Writes the reply in plain words in your language |

The panel on the right of the screen shows each agent as it works.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then add your keys
uvicorn app.main:app --reload
```

Open http://localhost:8000 (use `--port 8000`, or whichever port you pick).

It works without any keys: paste text or use the sample, and type your questions. Add keys to unlock more:

| Key | What it turns on |
| --- | --- |
| `GNANI_API_KEY` | Voice: Prisma listens, Timbre speaks |
| `GEMINI_API_KEY` | Reading photos of documents, a model-based reader, and replies in languages beyond Hindi and English |
| `TELEGRAM_BOT_TOKEN` | Voice messages on Telegram (see below) |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | Notifications on the phone itself (see below) |
| `INSURITHM_URL` | Sends details to the claim agent instead of only preparing them |

The microphone needs `localhost` or HTTPS.

## How the planner stays safe

The model picks from a fixed menu of ten tools and nothing else. Unknown tools are dropped, plans are capped at three steps, and if the model is down or answers badly the keyword router takes over. Each step is run by a normal agent and the reply is written from fixed templates (Hindi and English) or translated from them, so medicine names, doses and warning signs never come from the model's own words. The verifier's gate still holds back a whole plan while flagged items are unconfirmed. Quick-question chips skip the planner. Set `PLANNER=off` to use keywords only.

To see how routing scores on 20 hand-written requests (English, Hinglish, Hindi and a few other languages): `python -m app.eval_planner`. With `GEMINI_API_KEY` set it scores the planner on the same requests. It is a small sample, not a benchmark.

## Voice reminders on Telegram

1. In Telegram, message @BotFather, send `/newbot`, and copy the token it gives you.
2. Put it in `.env` as `TELEGRAM_BOT_TOKEN=...` (on Render, add it under Environment) and restart.
3. Read a summary, confirm any flagged items, then tap **Connect Telegram** in the Voice reminders card and press Start in Telegram.
4. Use **Send a test voice** to check it. After that the bot sends a Timbre voice message at each dose time (times are read in `TZ_NAME`, default Asia/Kolkata). Tap **Taken** under a message to tick it off; the website updates too.

You can also type to the bot ("what should I eat") and it answers with the same agents, and `/stop` pauses reminders.

Things to know:
- The server has to be awake at dose time. On a free host that sleeps when idle, point a free uptime pinger at `/api/health`.
- Linked reminders are saved in `data/linked.json`, so they survive a restart. A host with a throwaway disk loses them on redeploy; use a persistent disk or a database for real use.
- Run the bot from one place at a time. Two copies polling the same bot will fight over it.

## Notifications on the phone

This one needs no app and no account. It uses the browser's own push service, so it works on Android Chrome (and on iPhone once Carewise is added to the Home Screen).

1. Run `python -m app.vapid` and copy the two lines it prints into `.env` (on Render, add them under Environment). Keep the private one secret. Restart.
2. Open Carewise over HTTPS (your Render link, or `localhost` on the same machine), read a summary, confirm any flagged items, and tap **Turn on notifications** in the Reminders card. Allow the permission.
3. Tap **Send a test**. At each dose time a notification appears, even with Chrome closed. Tap **Taken** on the notification to tick the dose off, or tap the notification itself and Carewise opens and speaks the reminder in your language with Timbre.

Things to know:
- A website can't play a custom voice from a closed app, so the notification makes the phone's normal sound and the voice starts when you tap it. The Telegram channel is the one that delivers an actual voice message.
- The notification text shows the medicine names on the lock screen.
- It has the same hosting limits as Telegram reminders: the server must be awake at dose time, and a throwaway disk forgets subscriptions on redeploy (just tap Turn on notifications again after reading the summary).
- Only addresses on real push services (Google, Mozilla, Apple, Microsoft) are accepted as subscriptions.

## Languages

Hindi and English replies come from fixed templates, so the facts never drift. Bengali, Gujarati, Kannada, Malayalam, Marathi, Punjabi, Tamil and Telugu are translated by the model when `GEMINI_API_KEY` is set, and spoken by Timbre.

## Safety

- It explains and schedules only. It never diagnoses or changes a dose.
- The verifier holds back the timetable until unclear items are confirmed.
- Use fictional or consented documents for demos. Nothing is stored beyond the running session.

## Tests

```bash
python -m pytest -q
python -m app.eval     # scores the reader on the sample summaries in tests/fixtures
```

The three fixtures follow common discharge-summary layouts, so a high score there is a regression check for the basic reader, not a claim about every hospital's format. Run it with `GEMINI_API_KEY` set to compare the model reader.

## Layout

```
app/
  main.py          API routes
  orchestrator.py  runs the plan (or keyword routing) and the trace
  planner.py       the model chooses which agents run
  eval_planner.py  scores routing on sample requests
  gnani.py         Prisma and Timbre client
  llm.py           Gemini wrapper (optional)
  agents/          document, verifier, schedule, checkin, caregiver, handoff, explainer
  reminders.py     the clock watcher, the Telegram bot and phone notifications
  telegram.py      Telegram client       audio.py  WAV to MP3 for voice messages
  push.py          web push client       vapid.py  makes the notification keys
web/               the interface (plain HTML, CSS and JS) and sw.js, the notification service worker
tests/             unit and API tests, plus sample summaries
```
