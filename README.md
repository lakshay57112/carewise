# Carewise

Carewise explains a hospital discharge summary in plain language, in the patient's own language, and by voice.

You paste the summary or upload a photo. A small team of agents reads it, checks its own work, builds a medicine timetable, and then you can just talk to it ("agli dawai kaun si hai?"). Speech in and speech out run on Gnani's Prisma and Timbre models.

Built for the Great Indian AI Internship Challenge.

## What the agents do

| Agent | Job |
| --- | --- |
| Orchestrator | Works out what you asked and picks the right agent |
| Document | Reads the summary (text or photo) into medicines, diet, follow-up and warning signs |
| Verifier | Re-checks every medicine, dose and date against the original text. Anything unclear is sent back to you to confirm, and the timetable stays locked until you do |
| Schedule | Builds the day's timetable, finds the next dose, logs doses as taken |
| Check-in | Compares what you say ("mujhe bukhar hai") with the warning signs your own doctor wrote. It never diagnoses |
| Caregiver | Writes a short update for the family and carries any alert |
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
| `INSURITHM_URL` | Sends details to the claim agent instead of only preparing them |

The microphone needs `localhost` or HTTPS.

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
  orchestrator.py  intent routing and the trace
  gnani.py         Prisma and Timbre client
  llm.py           Gemini wrapper (optional)
  agents/          document, verifier, schedule, checkin, caregiver, handoff, explainer
web/               the interface (plain HTML, CSS and JS)
tests/             unit and API tests, plus sample summaries
```
