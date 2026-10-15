# Architecture

NoteTutor has two parts: a **video pipeline** (usable on its own from the
command line) and a **web application** that runs the pipeline for signed-in
users and stores the results in MongoDB.

```
                 +-------------------+        +------------------+
 Browser  --->   |  Nginx (web)      | /api   |  FastAPI (api)   |
 React app       |  static React app | -----> |  REST + worker   |
                 +-------------------+        +--------+---------+
                                                       |
                         subprocess per lesson         |  users, lessons,
                    +-------------------------+        |  GridFS files
                    |  Video pipeline (Python) |       v
                    |  temp working folder     |  +-----------+
                    +-----------+-------------+   |  MongoDB  |
                                |                 +-----------+
                  Groq / Gemini (LLM, vision)
```

## Video pipeline

Entry point: `app/pipeline/lesson_video.py`.

| Stage | Module | What it does |
|---|---|---|
| Read | `vision/transcription.py`, `ingestion/` | Images and scanned PDF pages are transcribed by a vision model; text PDFs are read directly. Long documents are split into ~5,000-character parts. |
| Terminology | `pipeline/lesson_video.py` | Writes technology names in their official form (e.g. a product name split by handwriting) - only spacing and case may change. |
| Structure | `vision/structure_extractor.py` | Turns the text into titled sections. |
| Fact check | `knowledge/verifier.py` | Checks the claims in the notes. |
| Plan | `teaching/planner.py` | Builds the lesson: scenes, teaching method, key points. The number of scenes and words per scene come from a length budget that grows slowly with the document. |
| Narrate | `content/generator.py` | Writes each scene's narration from the relevant part of the source. |
| Verify | `knowledge/verifier.py` | Checks the narration against the notes and corrects misplaced claims. |
| Visual plan | `video/visual_planner.py` | Chooses concept map, process flow, comparison, key points or illustration, with the sentence that introduces each element. Output is validated (duplicate concepts, label length, empty table cells, ...) and retried once with the problems listed. |
| Voice | `audio/tts.py` | Kokoro TTS, one clip per sentence, so every sentence's start and end time is known exactly. |
| Layout | `video/layout.py` | Deterministic layout: layered or radial graphs with label placement that avoids boxes and other arrows, wrapping process flows, auto-sized tables. Font size shrinks until everything fits. |
| Animate | `video/choreography.py`, `video/animator.py` | Turns sentence timings into reveal, focus and caption times and draws each frame (pop-in, spotlight, pulse, flowing dots). Static parts of the frame are cached as a layer. |
| Render | `video/renderer.py`, `audio/music.py` | MoviePy/FFmpeg encoding (CRF, tuned for flat graphics), background music ducked under speech. |
| Review | `pipeline/lesson_video.py` | An LLM judge lists any sentence that contradicts or is not in the source, with timestamps. |

### Reliability

- **Model fallback chain** (`common/llm_client.py`): Groq main model ->
  smaller Groq model -> Gemini -> optional local Ollama. Light tasks use the
  smaller model so the main model's per-minute limit is kept for content.
- **Rate-limit waiting**: long documents regularly hit the free tier's
  per-minute token limit; when Gemini is also busy, requests wait for the
  limit to reset (up to five minutes) instead of failing the lesson.
- **Fail fast**: if the online models are unavailable the run stops with a
  clear message instead of silently producing a low-quality lesson
  (`--allow-local` overrides).
- **Answer cache**: identical requests reuse earlier answers, so re-running
  the same material costs no quota (`--fresh` disables).
- **Lesson record**: every run writes `<video>.json` (source, structure,
  scenes, timings, visual plans) used by the evaluation scripts.

## Web application

### Backend (`app/api/`)

| File | Responsibility |
|---|---|
| `main.py` | App setup, CORS, startup (indexes, recovery of interrupted lessons) |
| `security.py` | bcrypt passwords, JWT (cookie or Bearer), `require_role` |
| `routers/auth.py` | register, login, logout, current user |
| `routers/lessons.py` | create (upload or topic), list, detail, video streaming with HTTP range requests, script, review, delete |
| `routers/admin.py` | users, roles, all lessons (admin only) |
| `worker.py` | one lesson at a time in a temporary folder; parses the pipeline log into stage and progress; stores results in GridFS |
| `db.py` | MongoDB connection, collections, GridFS |

**Data model**

- `users`: `email` (unique), `name`, `role` (`user` / `admin`),
  `password_hash`, `created_at`
- `lessons`: `owner_id`, `title`, `source_kind` (`file` / `topic`), `options`,
  `status` (`queued` -> `running` -> `done` / `failed`), `stage`, `progress`,
  `error`, `script`, `review`, `duration_seconds`, `scene_count`,
  `source_file_id`, `video_file_id`, `record_file_id`, timestamps
- GridFS (`fs.files`, `fs.chunks`): uploaded notes, videos, lesson records

**Upload limits** (sized to the free AI tiers): 10 MB, PDFs up to 10 pages,
text up to 40,000 characters.

### Frontend (`frontend/web/`)

React + TypeScript with Vite and Tailwind CSS. Pages: login / register,
dashboard (polls while lessons are generating), new lesson (drag-and-drop
upload or topic, voice and options), lesson view (player, download,
narration and fact review tabs), admin. Authentication uses the httpOnly
session cookie, so the token is never readable by JavaScript.

### Deployment

`docker-compose.yml` runs the API and Nginx (which serves the built React
app and proxies `/api`); MongoDB is either Atlas (`MONGO_URL`) or an
optional local container (`--profile local-db`).
