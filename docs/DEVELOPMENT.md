# Development guide

## Requirements

- Python 3.12
- Node.js 20
- Docker Desktop (for the full web app, or a local MongoDB)
- A free [Groq API key](https://console.groq.com/keys); optionally a free
  [Gemini API key](https://aistudio.google.com/apikey)

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # then fill in the values
```

### Voice models

Kokoro (default voice) - download both files into `models/tts/kokoro/`:

- `kokoro-v1.0.onnx`
- `voices-v1.0.bin`

from <https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0>.

Piper (fallback voice, optional) - download `en_US-lessac-medium.onnx` and
`en_US-lessac-medium.onnx.json` into `models/tts/` from
<https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium>.

## Environment variables

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Text and vision models (required) |
| `GEMINI_API_KEY` | Backup provider when Groq is busy (optional) |
| `JWT_SECRET` | Signs login tokens; 32+ random characters |
| `MONGO_URL` | MongoDB connection string (Atlas or local) |
| `ADMIN_EMAIL` | This e-mail becomes admin on registration (optional) |
| `MAX_UPLOAD_MB`, `MAX_PDF_PAGES` | Upload limits (default 10 / 10) |
| `NOTETUTOR_TTS_VOICE` | `af_heart`, `af_bella`, `am_michael`, `bf_emma`, `bm_george` |
| `NOTETUTOR_SPEECH_SPEED` | Narration speed (default `0.95`) |

## Running

### Everything in Docker

```bash
docker compose up --build                     # MongoDB from MONGO_URL
docker compose --profile local-db up --build  # with a local MongoDB
docker compose down                           # stop
```

Web app: <http://localhost:8080>.

### Without Docker (API + Vite dev server)

```bash
# MongoDB: set MONGO_URL to Atlas, or start a local one:
docker compose --profile local-db up -d db

uvicorn app.api.main:app --reload --port 8000
cd frontend/web && npm install && npm run dev  # http://localhost:5173
```

The Vite dev server proxies `/api` to `http://localhost:8000`; set
`API_URL` to use another port.

### Command line only

```bash
python -m app.pipeline.lesson_video samples/agentic_ai_notes.jpeg
```

The video, its lesson record (`.json`) and fact review (`.review.md`) are
written to `data/video/`.

## Tests

```bash
pytest                          # 56 tests: API (in-memory MongoDB), layout, sync, ingestion
cd frontend/web && npm test     # 9 tests: components, API client, forms
```

No API keys or database server are needed for the tests.

## Evaluation

```bash
python -m evaluation.eval_layout 300
python -m evaluation.eval_transcription samples/agentic_ai_notes.jpeg evaluation/data/agentic_ai_page1.txt
python -m evaluation.eval_lesson data/video/<lesson>.json --judge
```

See [../evaluation/RESULTS.md](../evaluation/RESULTS.md).

## Free-tier notes

- About 2-3 new lessons per day fit Groq's free daily quota; re-running the
  same material reuses cached answers.
- If Groq is busy, Gemini is asked; if both are busy the request waits for
  the per-minute limit to reset. A 10-page PDF therefore takes several
  minutes, much of it waiting. Only if nothing answers for several minutes
  does the run stop with a message rather than use a weaker local model.
  "Try again" then continues quickly from the cached answers.
- MongoDB Atlas free clusters (512 MB) pause after long inactivity - resume
  them from the Atlas dashboard.
