# Clipforge unified plan: audit and proposed architecture

Status: **approved with the recommended defaults** (2026-11-22): Vite + React, YuNet,
fallback order Groq → Gemini → Ollama → rules. **Phase 1 is built** (not committed yet).

## 1. Audit: most of the brief already exists

Clipforge already has most of what the brief asks for. `docs/PLAN.md` phases 1–5 were
built on this branch line. So the work is mainly **closing gaps and hardening**,
not a rewrite.

| Brief item | What exists today | Gap |
|---|---|---|
| Signal fusion (audio + motion + transcript) | `recaply/signals/` (audio_energy, audio_features, shout_laugh, motion, chat_rate), `score/fusion.py`, `score/windows.py`; weights per content profile | Works. librosa isn't needed: numpy/scipy already measure the same features. Chat replay breaks on a bad `cookies.txt` with an unclear error. |
| Local-first transcription | `providers/transcribe/`: faster-whisper (default), Groq Whisper with local fallback, OpenAI | Whisper **hallucinates on silence/music** (the "आप आप आप…" clip title). No repeat/no-speech guard. |
| Local LLM scoring | `providers/llm.py`: Ollama (default), Gemini, Claude, OpenAI; `score/rerank.py` with a rule-based fallback | **One 0–100 score**, not 4 axes. **No Groq LLM.** No fallback chain: if the chosen provider is down it drops straight to rules (that's the "AI offline" / "Rule-based" you saw), never to another provider. |
| Titles, descriptions, hashtags, hook text | rerank already returns title, hook, description, hashtags and emphasis words | None. Show them better in the UI. |
| Story beats | `score/beats.py` | Needs an LLM, empty without one. |
| Karaoke captions, kinetic pop | `render/subtitles.py`: 9 presets, word highlight, pop, keywords, emoji, safe zones (drawn by the editor renderer, not ASS) | Add named presets **Bold & Yellow / Minimal White / Neon**, a real **bounce/stagger** animation, and an optional `.ass` export for use in other editors. |
| Smart auto-framing | `render/reframe.py`: YuNet face tracking, a smoothed virtual camera with a dead zone, plus split / action / center / fit / auto | Works. **MediaPipe isn't recommended** (no reliable Python 3.14 wheels). YuNet already does BlazeFace's job. Gap: no plain "Auto-reframe vs Center crop" switch in the UI. |
| B-roll (Pexels) | `edit/broll.py` (Pexels with a key, Openverse without) | Works. Needs a key field in Settings. |
| Auto-BGM with ducking | — | **Missing.** |
| Cinematic teaser hook (bars, glitch) | `hook_layer` = text box only | **Missing.** |
| Dashboard | `frontend/` Vite + React + Tailwind + Radix: Dashboard (link + file upload), Project (player + beats/clips split), Director (trim, captions, reframe, export). Typechecks, 12 tests pass, builds. | Progress is a free-text stage name, with **no stepper** (Downloading → Analyzing → Transcribing → Scoring → Rendering). Failures show as one long banner, not "failed at step X, because Y". No 4-axis score display. |
| Retries | `providers/http.py`: retries on network errors, 429 and 5xx | Retries are per call only. No cross-provider fallback (see above). |
| Live streams | `recaply/live/`: rolling segment recorder, near-real-time detection, chat listeners, clip pipeline, notifications | Built, but untested against real Twitch/Kick streams. |
| Job queue | `recaply/jobs/` (SQLite store, threads or RQ) | Store a structured `stage` + `error` + per-stage log for the UI. |

**Recommendation on the frontend:** keep **Vite + React**, not Next.js. It's
already built, typed, tested and served by Flask on one port with no CORS.
Next.js would add a second server and a rewrite and wouldn't fix anything.

**Known blocker:** I still couldn't open the logged-in dashboard in a browser
to check it live. Fixing that is step 0 below.

## 2. Target architecture (unchanged, not a rewrite)

```
Flask (app.py, :5000) ── serves ─▶ React studio (/studio, built from frontend/)
   │  /api/studio/*, /api/clipjob/*, /clipjob/*
   ▼
recaply.jobs (SQLite queue) ─▶ recaply.engine.analyze
   ingest (yt-dlp audio only) ─▶ signals ─▶ fusion ─▶ windows
   ─▶ transcribe (TranscriberChain) ─▶ rerank (LLMChain, 4-axis) ─▶ beats ─▶ select
   ─▶ render.shorts (reframe ▸ captions ▸ hook teaser ▸ b-roll ▸ BGM duck) ─▶ editor.render (ffmpeg)
```

New pieces are marked **(new)** below. Everything else is existing code.

## 3. Phases

### Phase 1: backend hardening and the local-first pipeline (implement right after approval)
1. **LLMChain (new)** in `providers/llm.py`: try the configured provider → other
   configured cloud providers → Ollama → rules. Each step uses the existing retries.
   The job summary records which one answered and why the others were skipped.
2. **Groq LLM provider (new)**: OpenAI-compatible chat on `api.groq.com`
   (`llama-3.3-70b-versatile` by default), using the `GROQ_API_KEY` already used for Whisper.
3. **4-axis virality score**: rerank JSON gets `hook_score`, `engagement_score`,
   `value_score`, `shareability_score` (0–10) and `overall_virality` (0–100, a
   weighted mix, re-checked in code so the model can't make it up). The rules
   fallback fills the same axes from the signals. Old jobs keep working (axes = null).
4. **Whisper hallucination guard (new)**: VAD on, `no_speech_threshold` and
   `compression_ratio_threshold` set, and a post-filter that drops segments with
   heavy word repetition or low average log-probability. Titles never come from a dropped segment.
5. **Cookie check**: validate `cookies.txt` before use. If it isn't Netscape
   format, skip it with a one-line, fixable note instead of the yt-dlp traceback.
6. **Structured job stages**: fixed stages `download → analyze → transcribe →
   score → render`, each with state / progress / started / finished / error, kept in
   the job store and returned by `/api/studio/projects` and a new `/api/clipjob/<id>/log`.
7. **Tests** for each item, in the same style as `tests/test_phase1_local_ai.py`.

### Phase 2: dashboard
1. **Step 0:** get a logged-in browser on `/studio` and audit it live (console, network, layout).
2. A stage **stepper** with a progress bar per project (dashboard card and project page).
3. An **error panel**: "Failed at Transcribing: <reason> · View log · Retry this step".
4. **Clip cards** show the 4 axes, title, description and hashtags (copy buttons).
5. Director: an **Auto-reframe / Center crop** switch and the new caption presets
   in the existing trim and caption UI. A **5-second low-res preview** before a full render.
6. **Settings page**: AI provider order, Groq / Gemini / Pexels keys, Ollama status with a "test" button.

### Phase 3: creative features
1. **Caption presets** Bold & Yellow, Minimal White, Neon, plus bounce and stagger animations; an optional `.ass` export.
2. **Cinematic teaser hook (new)**: pick the strongest frame (motion + face + sharpness),
   3 s with a dark overlay, letterbox bars and hook text, then a glitch transition (RGB split + noise, in ffmpeg) into the clip. Switchable per export.
3. **Auto-BGM (new)**: `assets/bgm/` (user-supplied, a mood picked by the profile),
   ducked under speech with ffmpeg `sidechaincompress` keyed on the voice track. Loudness normalised to -14 LUFS.
4. B-roll: the existing Pexels module plus the Settings key and a per-clip on/off.

### Phase 4: live streams
1. Test the existing recorder and detector end to end on a real Twitch and Kick stream. Fix whatever breaks.
2. Live page: channel status, the rolling buffer, captured moments, one-click "clip the last N seconds".
3. Reconnect and backoff for dropped streams. Limit disk use of the segment buffer.

## 4. Decisions needed before Phase 1
1. Keep **Vite + React** (recommended) or move to Next.js?
2. Keep the **YuNet** face tracker (recommended) or add MediaPipe anyway?
3. LLM fallback order: **Groq → Gemini → Ollama → rules** when keys exist,
   Ollama first otherwise (recommended)?
