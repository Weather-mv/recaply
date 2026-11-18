# Recaply: Claude Code Master Prompt

Paste everything below the line into Claude Code, from the root of your existing Valorant highlight tool repo.

---

## ROLE

You are a senior full-stack and video-pipeline engineer. We are upgrading **Recaply**, an existing Valorant-specific highlight finder, into a fast, efficient, **game-agnostic and stream-agnostic AI clipping platform** with feature parity with Opus Clip, plus live-stream clipping. It must work for gaming (GTA 6, Valorant, any game), podcasts, interviews, IRL streams, webinars, and talk content.

## STEP 0: DISCOVER BEFORE YOU BUILD (do this first, no code changes yet)

1. Read the entire existing repo. Summarize: stack, folder structure, how highlight detection currently works, what is Valorant-specific (kill-feed OCR, agent names, keywords, etc.), and what is reusable.
2. Propose a refactor plan that moves all Valorant-specific logic into a **pluggable "game profile"** module (so Valorant becomes one profile among many) and keeps everything else generic.
3. Show me the plan and the target architecture, and wait for my approval before large rewrites. Keep the existing stack unless there is a strong reason to change it, and explain any change.

## PRODUCT GOALS

1. **Input anything:** YouTube/Twitch/Kick links (VODs and live), direct file upload, and local paths.
2. **Output:** ranked, captioned, vertical-ready short clips with titles, hooks, and descriptions.
3. **Fast:** a 1-hour video should produce candidate clips in a few minutes on a normal machine. Never download or process more than necessary.
4. **Efficient:** minimal API cost per video. Use cheap signals first and expensive models only on shortlisted candidates.
5. **Universal:** content type is auto-detected and the scoring weights adapt (see "Content profiles").

## FEATURE PARITY WITH OPUS CLIP (build all, in the phases below)

- AI clip discovery with a virality score (0-100) and a short human-readable reason per clip
- Automatic clip length selection (configurable 15-90s) with clean start/end points (cut on sentence or beat boundaries, never mid-word)
- Auto reframe to 9:16 / 1:1 / 16:9: face and speaker tracking for talking content, smart center/action crop for gameplay (plus optional blurred-background fit and split-screen layout: facecam on top, gameplay below)
- Animated word-by-word karaoke captions (multiple style presets, emoji and keyword highlighting, custom fonts and colors, safe-zone aware)
- Filler-word and long-silence removal (toggle)
- AI hook generator (first-3-seconds text hook), title, description, and hashtags per clip
- AI B-roll insertion from free sources (Pexels/Openverse) matched to keywords (toggle)
- Brand kit: logo/watermark, colors, fonts, intro/outro, saved per workspace
- Text-based clip editor: edit the transcript to trim or reorder, with timeline preview and manual start/end adjustment
- Multi-language transcription and caption translation
- Batch processing and a project dashboard (queue, status, retry)
- Export presets and direct publishing/scheduling to YouTube Shorts, TikTok, and Instagram Reels (use official APIs, or an integration like Upload-Post, behind a clean interface)
- **Beyond Opus: live-stream mode** (see Phase 4)

## ARCHITECTURE REQUIREMENTS

- **Modular pipeline** with clear stages and interfaces: `ingest → analyze → score → select → render → publish`. Each stage is independently testable and replaceable.
- **Job queue with workers** (Redis + RQ/Celery or the equivalent in the existing stack), resumable jobs, per-stage caching on disk keyed by content hash, and progress events streamed to the UI (SSE or WebSocket).
- **Provider abstraction** for each AI dependency, switchable via config/env:
  - Transcription: `faster-whisper` (local, default), Groq Whisper API (fast cloud), OpenAI Whisper API
  - LLM scoring and copywriting: Gemini Flash (default, free tier), Claude Haiku, OpenAI mini, Ollama (local)
- **Signal plugins** (each outputs a normalized 0-1 score per time window, so new signals can be added without touching the core): transcript/LLM, audio energy and laughter/shout peaks, chat-message rate and emote spikes, scene-change/motion intensity, and optional game-profile detectors (e.g. Valorant kill-feed OCR).
- **Game profiles** as config plus optional detector plugins (Valorant, GTA, generic FPS, generic story/open-world, "talk/podcast", "IRL"). A profile defines signal weights, keyword lists, reframe strategy, and clip-length defaults.
- Config in a single typed settings file plus `.env`. No hard-coded keys or paths.

## PERFORMANCE RULES (non-negotiable)

1. **Cheap-first funnel:** run audio-energy, chat-spike, and motion analysis over the whole video (cheap), shortlist the top ~15-30 candidate windows, and only then run Whisper and LLM scoring on those windows. Never send a full multi-hour transcript to an LLM if windows can be used.
2. **Download only what's needed:** extract audio-only for analysis; download full video only for the final selected segments (yt-dlp section download or ffmpeg `-ss/-to` on the stream). Never download a 5-hour VOD in full unless the user asks.
3. **Parallelism:** transcribe chunks in parallel, render clips in parallel workers, and use GPU encoding (NVENC/VideoToolbox/VAAPI) when available with automatic CPU fallback. Prefer stream-copy cuts when no re-encode is needed.
4. **Caching and idempotency:** every stage caches output, so re-running with new settings (different style, aspect ratio) must not repeat download or transcription.
5. **Chunk long inputs** (30-min windows with overlap) with a bounded memory footprint.
6. Add simple timing and cost logging per stage (seconds, API tokens, estimated $) and show it in the job summary.

## CONTENT PROFILES AND AUTO-DETECTION

Auto-detect the content type from the first few minutes (speech ratio, number of speakers, presence of facecam/HUD, chat availability, title and category metadata) and pick a profile, with a manual override in the UI.

- **Talk/podcast/interview:** transcript and LLM dominate scoring (hooks, opinions, stories, quotable lines).
- **Gaming (any game):** chat spikes, audio peaks, streamer reaction (shouting/laughing), motion/scene intensity, and transcript keywords dominate. No assumptions about kill feeds. Valorant keeps its extra OCR detector as an optional plugin.
- **IRL/variety:** chat plus audio plus transcript, balanced.
- Scoring = weighted fusion of signals plus an LLM re-rank of the shortlist. Boost windows where multiple independent signals overlap. Penalize dead air, repeated content, and intro/outro filler. De-duplicate overlapping clips.

## PHASES (implement in order; commit after each; stop and show me a demo plus summary at the end of each)

**Phase 1: Core pipeline MVP.** Refactor Valorant-specific code into a game profile. URL/file → audio extract → faster-whisper (Groq optional) → candidate windows → LLM scoring → cut clips with FFmpeg. CLI first, then a minimal web UI. Job queue plus caching.

**Phase 2: Opus-style output.** 9:16 reframing (face tracking and gameplay crop strategies, split-screen layout), karaoke captions with presets, hook/title/description generation, filler/silence removal, export presets, brand kit.

**Phase 3: Multi-signal scoring.** Audio energy/laughter detection, scene/motion scoring, chat-replay ingestion for Twitch/YouTube/Kick VODs (public chat replay endpoints, respecting platform terms), content-type auto-detection, signal fusion, and the cheap-first funnel with measured speedups.

**Phase 4: Live-stream mode.** Record live streams with `streamlink`/`yt-dlp` in rolling 5-minute segments. Run chat-spike and audio detection in near real time. When a spike fires, cut the clip from the buffer (e.g. 30s before to 15s after), then run the normal Phase 2 pipeline in the background. Support multiple channels, a per-channel config, a "clip ready" notification (webhook/Discord/in-app), and an auto-stop when the stream ends.

**Phase 5: Editing and publishing.** Text-based transcript editor with timeline, B-roll insertion, translation, a publishing/scheduling interface with at least one working platform, batch queue UI, and a usage/cost dashboard.

## ENGINEERING STANDARDS

- Typed code, small modules, docstrings on public interfaces, and structured logging.
- Unit tests for scoring/fusion and cut-boundary logic, plus an integration test using a short sample video in `tests/fixtures`.
- A `README.md` with setup (including FFmpeg, yt-dlp, streamlink install), env vars, architecture diagram (Mermaid), and a "how to add a game profile / signal plugin" guide.
- Docker Compose for one-command local run (app, worker, Redis), with an optional GPU profile.
- Graceful error handling and retries for network failures, unavailable chat, missing captions, and rate limits. Fall back to the next-best signal rather than failing the job.
- Security: sanitize URLs and filenames, no shell injection (use argument lists for subprocesses), and keep API keys out of logs.

## LEGAL AND PLATFORM NOTES

Add a clear note in the README and a UI notice: users must have the rights or permission to clip and repost the source content, and must follow each platform's terms. Do not add features that strip watermarks or bypass DRM or platform protections.

## HOW TO WORK WITH ME

- Start with Step 0 and ask me up to 5 clarifying questions at once (e.g. hosting target, GPU availability, the platforms to publish to first, whether it is for my own use or a paid SaaS). Do not ask them one at a time.
- Make reasonable decisions yourself on small details and list them in the end-of-phase summary.
- At the end of each phase, give me: what was built, how to run it, measured performance (seconds per hour of video, estimated cost per video), known limitations, and your recommended next step.
