"""
The clipping pipeline: ingest -> analyze -> score -> select (-> cut).

    from recaply.engine import Options, analyze, cut_clips
    result = analyze("https://youtu.be/...", Options(count=8))
    files = cut_clips(result, [c["id"] for c in result["clips"]], out_dir)

Cheap-first funnel: loudness (and later chat and motion) are measured over
the whole video; only the shortlisted windows are transcribed and sent to
the LLM. Talk profiles transcribe everything instead (loudness says little
about podcasts), then pre-filter on the text before the LLM sees anything.
Every stage is cached per source, so re-running with other settings repeats
no download and no transcription.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Callable

import numpy as np

from common import fmt_hms
from recaply import signals as _signals  # noqa: F401  (registers built-in signal plugins)
from recaply.cache import StageCache, safe_name
from recaply.ingest import audio as audio_ingest
from recaply.ingest import chat as chat_ingest
from recaply.ingest.sources import InputError, Source, resolve
from recaply.log import get_logger
from recaply.profiles import cues as cues_mod
from recaply.profiles import detect_profile, get_profile
from recaply.providers.llm import get_llm, ollama_status
from recaply.providers.transcribe import get_transcriber, transcribe_cached
from recaply.providers.transcribe.base import BLOCK, blocks_for
from recaply.score.beats import beat_score, clip_beats, score_beats
from recaply.score.boundaries import snap
from recaply.score.fusion import fuse
from recaply.score.rerank import headline, rerank
from recaply.score.select import select
from recaply.score.windows import (peak_windows, probe_windows, text_score, top_windows,
                                   words_between)
from recaply.settings import settings
from recaply.signals.audio_energy import dead_air, to_db
from recaply.signals.base import SignalContext, fit_length, get_signal
from recaply.telemetry import Telemetry

log = get_logger("engine")
Progress = Callable[[str, float], None]


@dataclass
class Options:
    """What the user asked for. None means "the profile's / settings' default"."""
    profile: str = "auto"
    count: int = 10
    min_len: float | None = None
    max_len: float | None = None
    language: str | None = None
    transcriber: str | None = None
    llm: str | None = None
    shortlist: int | None = None
    allow_local: bool = True
    chat_file: str | None = None        # a chat export for a local file (Phase 3)


def _noop(stage: str, frac: float) -> None:
    pass


def measure_cues(ctx: SignalContext, chat: list[dict] | None, tel: Telemetry) -> cues_mod.Cues:
    """Cues for content-type detection from the first minutes (see recaply.profiles.cues)."""
    from recaply.signals import audio_features, motion
    c = cues_mod.Cues()
    with tel.stage("audio_features", cached=ctx.cache.get_array("audiofeat", {"v": audio_features.VERSION}) is not None):
        c.speech_ratio = cues_mod.speech_ratio(audio_features.get(ctx))
    if chat is not None:
        c.chat_per_min = round(len(chat) * 60.0 / max(1.0, ctx.duration), 2)
    elif ctx.source.kind == "url":
        c.chat_per_min = None
    if ctx.source.kind == "file" and ctx.source.has_video:        # links would need a video download: skipped
        try:
            raw = motion.raw(ctx)
            head = raw[:cues_mod.HEAD_SEC, 0]
            c.motion = float(np.mean(head)) if len(head) else None
            c.face_area = cues_mod.face_area(ctx.source.location, ctx.duration)
        except Exception as e:  # noqa: BLE001
            tel.note(f"picture cues skipped: {str(e)[:100]}")
    return c


def analyze(source: str | Source, opts: Options | None = None, progress: Progress | None = None,
            telemetry: Telemetry | None = None) -> dict:
    """Find, score and rank clips. Returns a JSON-safe result (see README "Clip jobs")."""
    opts = opts or Options()
    progress = progress or _noop
    tel = telemetry or Telemetry()

    # ---- ingest
    progress("Reading the link", 0.0)
    with tel.stage("resolve"):
        src = source if isinstance(source, Source) else resolve(source, allow_local=opts.allow_local)
    if src.is_live:
        state = (getattr(src, "raw", None) or {}).get("live_status")
        when = ("This stream has just ended and YouTube is still preparing the recording. Try again in a while."
                if state == "post_live" else "This stream is live right now.")
        raise InputError(when + " Clips from a live stream come from Live mode: run "
                         "'python -m recaply live run <link>', or use the Live page in the desktop app. "
                         "Once the stream has ended and the recording is ready, paste the link here to clip the whole video.")
    auto = opts.profile in ("", "auto")
    det = detect_profile(src.meta) if auto else None
    weak = bool(auto and cues_mod.is_weak(det))
    try:
        profile = get_profile(det.profile if auto else opts.profile)
    except KeyError:
        raise InputError(f"Unknown profile '{opts.profile}'.") from None
    if profile.segmenter:
        raise InputError(f"The '{profile.label}' profile runs in rounds mode in the app.")
    cache = StageCache(src.key)
    log.info("analyze", source=src.key, title=src.title[:60], profile=profile.key, duration=round(src.duration))

    # chat replay is fetched in the background while the audio comes down
    chat_pool = ThreadPoolExecutor(1)
    chat_future = None
    if opts.chat_file:
        chat_future = chat_pool.submit(lambda: chat_ingest.load_file(opts.chat_file, src.duration) or None)
    elif src.kind == "url" and settings.chat_replay and (weak or profile.weights.get("chat_rate", 0) > 0):
        chat_future = chat_pool.submit(chat_ingest.fetch, src, cache, None, tel.note, True)

    had_audio = src.kind == "file" or any(n.startswith("audio.") and not n.endswith(".part")
                                          for n in os.listdir(cache.dir))
    progress("Downloading & Extracting Audio...", 0.02)
    with tel.stage("audio", cached=had_audio):
        apath = audio_ingest.ensure_audio(src, cache, progress=lambda f: progress("Downloading & Extracting Audio...",
                                                                                   0.02 + 0.2 * f))

    progress("Analyzing Audio Energy (Finding Hype Moments)...", 0.22)
    hit = cache.get_array("rms", {"v": 1})
    with tel.stage("loudness", cached=hit is not None):
        rms = hit if hit is not None else cache.array("rms", {"v": 1}, lambda: audio_ingest.loudness(
            apath, src.duration, workers=settings.workers,
            progress=lambda f: progress("Analyzing Audio Energy (Finding Hype Moments)...", 0.22 + 0.1 * f)))
    duration = float(src.duration or len(rms))
    n = len(rms)

    chat: list[dict] | None = None
    if chat_future is not None:
        with tel.stage("chat", cached=bool(cache.get_json("chat", {"v": chat_ingest.VERSION}))):
            try:
                chat = chat_future.result()
            except Exception as e:  # noqa: BLE001 — chat is optional
                tel.note(f"chat replay not read: {str(e)[:120]}")
    chat_pool.shutdown(wait=False)

    # ---- content type: metadata first; when it has nothing decisive, measure the first minutes
    profile_reason = "chosen" if not auto else f"auto: {det.reason}"
    measured = None
    ctx = SignalContext(source=src, profile=profile, cache=cache, audio_path=apath, duration=duration, rms=rms,
                        extras={"chat": chat} if chat else {})
    if weak:
        progress("Checking what kind of video this is", 0.3)
        with tel.stage("detect"):
            measured = measure_cues(ctx, chat, tel)
            det2 = cues_mod.classify(measured)
        profile = get_profile(det2.profile)
        profile_reason = f"auto: {det2.reason} ({measured.describe()})"
        ctx.profile = profile
    profile = profile.with_lengths(opts.min_len, opts.max_len)
    ctx.profile = profile
    if src.kind == "url" and not settings.chat_replay and profile.weights.get("chat_rate", 0) > 0:
        tel.note("chat replay is switched off (RECAPLY_CHAT=0)")

    # ---- analyze: cheap signals over the whole video, in parallel
    progress("Analyzing Audio Energy (Finding Hype Moments)...", 0.32)
    sigs: dict[str, np.ndarray] = {}
    todo = []
    for name, weight in profile.weights.items():
        plugin = get_signal(name)
        if weight > 0 and plugin is not None:
            todo.append((name, plugin))

    def run_signal(item):
        name, plugin = item
        try:
            if not plugin.available(ctx):
                return name, None, None
            params = {"v": plugin.version, **plugin.cache_params(ctx)}
            hit = cache.get_array(f"sig-{name}", params)
            with tel.stage(f"signal:{name}", cached=hit is not None):
                arr = hit if hit is not None else cache.array(f"sig-{name}", params, lambda: plugin.compute(ctx))
            return name, fit_length(arr, n), None
        except Exception as e:  # noqa: BLE001 — fall back to the other signals
            log.warning("signal failed", signal=name, error=str(e)[:200])
            return name, None, str(e)[:120]

    with tel.stage("signals"):
        if todo:
            with ThreadPoolExecutor(len(todo)) as ex:
                for name, arr, err in ex.map(run_signal, todo):
                    if arr is not None:
                        sigs[name] = arr
                    elif err:
                        tel.note(f"signal {name} skipped: {err}")
    missing = [k for k in profile.weights if k not in sigs and k != "transcript"]
    if missing:
        tel.note("signals not available: " + ", ".join(
            f"{k} (no chat replay)" if k == "chat_rate" else k for k in missing))
    fused = fuse(sigs, profile.weights, n, dead=dead_air(rms))

    # ---- shortlist (+ transcripts)
    k = opts.shortlist or settings.shortlist_size or (24 if profile.kind == "talk" else 16)
    transcriber = None
    try:
        transcriber = get_transcriber(opts.transcriber, note=tel.note)
    except ValueError as e:
        tel.note(f"transcription off: {e}")
    text_w = profile.weights.get("transcript", 0.0)
    words: list[dict] = []
    vocab = profile.vocab

    funnel = {"audio_sec": round(duration, 1), "transcribed_sec": 0.0}

    seen_blocks: set[int] = set()

    def do_transcribe(spans, label, base, span):
        nonlocal transcriber
        if transcriber is None:
            return []
        label = f"Transcribing Moments ({getattr(transcriber, 'label', 'Local GPU/CPU')})..."
        seen_blocks.update(blocks_for(spans, duration))              # the 30 s blocks it needs, over all passes
        funnel["transcribed_sec"] = round(min(duration, len(seen_blocks) * BLOCK), 1)
        try:
            with tel.stage("transcribe"):
                return transcribe_cached(transcriber, apath, cache, spans, duration, vocab=vocab,
                                         language=opts.language, telemetry=tel,
                                         progress=lambda f: progress(label, base + span * f))
        except Exception as e:  # noqa: BLE001 — score on signals alone rather than fail
            log.warning("transcription failed", provider=transcriber.name, error=str(e)[:200])
            tel.note(f"transcription failed ({transcriber.name}): {str(e)[:150]}")
            transcriber = None
            return []

    if profile.transcribe_all and transcriber is not None:
        # speech-heavy video: never transcribe it all (quota!). Probe it, then go deep on the best spots.
        progress("Extracting Top Moments...", 0.33)
        peaks = peak_windows(fused, profile, max(3, k // 4), duration, signals=sigs)
        pool = peaks + probe_windows(fused, profile, duration, settings.talk_probe_every, settings.talk_probe_len)
        for w in peaks:                      # a peak is probed on the one block around it, like the even samples
            b0 = int(w.peak // BLOCK) * BLOCK
            w.probe = (float(b0), min(duration, b0 + BLOCK))
        first = [w.probe for w in pool]
        words = do_transcribe(first, "Transcribing", 0.34, 0.2)                    # pass 1: probes + peaks
        for w in pool:
            a, b = w.probe or (w.start, w.end)
            w.text_score = text_score(words, profile, a, b)
        windows = top_windows(pool, k, text_w if words else 0.0)
        # rolling stop: with plenty of candidates, drop quiet windows whose probe has no clippable speech
        if len(windows) > 2 * max(1, opts.count):
            keep = [w for w in windows if w.text_score > 0.05 or w.signal_score > 0.25]
            windows = keep if len(keep) >= opts.count else windows
        deep, used = [], set(seen_blocks)        # best windows first, until the audio budget is spent
        budget = settings.talk_max_share * duration
        kept = []
        for w in windows:
            span = (max(0.0, w.start - 8), min(duration, w.end + 8))
            blocks = used | set(blocks_for([span], duration))
            if kept and len(kept) >= max(1, opts.count) and len(blocks) * BLOCK > budget:
                continue
            used, kept = blocks, kept + [w]
            deep.append(span)
        windows = kept
        words = do_transcribe(deep, "Transcribing", 0.54, 0.2)                     # pass 2: full windows (probe blocks cached)
        for w in windows:
            w.words = words_between(words, w.start - 10, w.end + 10)
            w.text_score = text_score(words, profile, w.start, w.end)
    else:
        progress("Extracting Top Moments...", 0.33)
        windows = peak_windows(fused, profile, k, duration, signals=sigs)
        spans = [(max(0.0, w.start - 8), min(duration, w.end + 8)) for w in windows]
        words = do_transcribe(spans, "Transcribing the best moments", 0.34, 0.4)
        for w in windows:
            w.words = words_between(words, w.start - 10, w.end + 10)
            w.text_score = text_score(words, profile, w.start, w.end)
    for w in windows:
        if not w.signals:
            w.signals = {name: round(float(v[int(w.start):int(w.end) + 1].max()), 3)
                         for name, v in sigs.items() if len(v[int(w.start):int(w.end) + 1])}

    # ---- score: LLM re-rank of the shortlist
    llm = get_llm(opts.llm)
    if llm is not None and llm.provider == "ollama":
        st = ollama_status(settings.ollama_url, llm.model)
        if not (st["running"] and st["model_ready"]):
            tel.note(f"local AI not ready: {st['hint']} Scored by rules instead.")
            llm = None
    if llm is None:
        tel.note(f"no LLM configured ({opts.llm or settings.llm_provider}); heuristic scores")
    progress("Scoring with AI" if llm else "Scoring", 0.76)
    with tel.stage("score"):
        suggestions, scorer = rerank(windows, profile, src.title, llm, tel)

    # ---- story beats: label what each shortlisted moment does (hook / setup / build / payoff ...)
    beats_by_window: list[list[dict]] = [[] for _ in windows]
    if llm is not None and settings.story_beats and scorer != "heuristic":
        progress(f"Story Beat Analysis ({llm.provider.capitalize()})...", 0.86)
        with tel.stage("beats"), ThreadPoolExecutor(1 if llm.provider == "ollama" else 3) as ex:
            beats_by_window = list(ex.map(
                lambda ws: score_beats(words_between(ws[0].words, ws[1]["start"], ws[1]["end"]), llm, profile,
                                       src.title, tel) if ws[0].words else [],
                zip(windows, suggestions)))

    # ---- select: clean boundaries, de-duplicate, rank
    db = to_db(rms)
    clips = []
    for i, (w, sug) in enumerate(zip(windows, suggestions), 1):
        s, e = snap(w.words, sug["start"], sug["end"], profile.clip_min, profile.clip_max, duration, energy=db)
        cw = words_between(w.words, s, e)
        beats = clip_beats(beats_by_window[i - 1], s, e)
        bscore = beat_score(beats)
        if bscore is not None:          # the story pass nudges the rank a little: 80% as before, 20% best payoff
            sug["score"] = round(0.8 * sug["score"] + 0.2 * bscore, 1)
        if sug.get("llm_score") is None:           # rule-based: title from the final clip's best line
            sug["title"] = headline(cw, profile) or f"Moment at {fmt_hms(w.peak)}"
            sug["description"] = sug.get("description") or sug["title"]
        clips.append({
            "id": i, "start": s, "end": e, "duration": round(e - s, 2), "time": fmt_hms(s),
            "score": sug["score"], "llm_score": sug.get("llm_score"),
            "signal_score": round(100 * w.signal_score, 1), "text_score": round(100 * w.text_score, 1),
            "reason": sug["reason"], "title": sug["title"], "hook": sug["hook"],
            "description": sug.get("description") or "", "hashtags": sug.get("hashtags") or [],
            "emphasis": sug.get("emphasis") or [], "beats": beats, "beat_score": bscore,
            "transcript": " ".join(x["w"] for x in cw)[:1500], "signals": w.signals,
            "window": [round(w.start, 1), round(w.end, 1)],
        })
    with tel.stage("select"):
        chosen = select(clips, opts.count)
    progress("Done", 1.0)
    return {
        "source": src.public(),
        "source_path": src.location if src.kind == "file" else None,   # local jobs only
        "profile": {"key": profile.key, "label": profile.label, "reason": profile_reason,
                    "clip_min": profile.clip_min, "clip_max": profile.clip_max},
        "scorer": scorer, "transcriber": transcriber.cache_id if transcriber else None,
        "clips": chosen, "shortlist": len(windows), "options": {**asdict(opts), "chat_file": None},
        "signals_used": sorted(sigs), "chat_messages": len(chat or []),
        "cues": measured.public() if measured else None,
        "funnel": {**funnel, "share": round(funnel["transcribed_sec"] / duration, 3) if duration else 0.0,
                   "windows": len(windows), "llm_calls": sum(t["calls"] for t in tel.tokens.values())},
        "summary": tel.summary(),
    }


def cut_clips(result: dict, ids: list[int], out_dir: str, quality: str = "1080",
              source: Source | None = None, progress: Callable[[int, str, float], None] | None = None,
              telemetry: Telemetry | None = None) -> dict[int, dict]:
    """
    Cut the clips with these ids from the source into `out_dir`. Returns
    {id: {"file", "encoder"} or {"error"}}. Clips are cut in parallel.
    """
    from recaply.render.cut import clip_inputs, cut
    tel = telemetry or Telemetry()
    os.makedirs(out_dir, exist_ok=True)
    by_id = {c["id"]: c for c in result["clips"]}
    todo = [by_id[i] for i in ids if i in by_id]
    if not todo:
        return {}
    s = result["source"]
    with tel.stage("resolve"):
        src = source or resolve(s["location"] if s["kind"] == "url" else _local_path(result), allow_local=True)
        inputs = clip_inputs(src, quality)
    out: dict[int, dict] = {}

    def one(c):
        name = f"{c.get('rank', c['id']):02d}_{safe_name(c.get('title') or 'clip', 40)}_{fmt_hms(c['start']).replace(':', '-')}.mp4"
        path = os.path.join(out_dir, name)
        try:
            if progress:
                progress(c["id"], "cutting", 0.0)
            enc = cut(inputs, c["start"], c["end"], path, has_video=src.has_video,
                      progress=(lambda f: progress(c["id"], "cutting", f)) if progress else None)
            out[c["id"]] = {"file": name, "encoder": enc, "size_mb": round(os.path.getsize(path) / 1e6, 1)}
            if progress:
                progress(c["id"], "done", 1.0)
        except Exception as e:  # noqa: BLE001
            out[c["id"]] = {"error": str(e)[:300]}
            if progress:
                progress(c["id"], "failed", 0.0)

    with tel.stage("cut"), ThreadPoolExecutor(max(1, settings.clip_workers)) as ex:
        list(ex.map(one, todo))
    return out


def _local_path(result: dict) -> str:
    p = result.get("source_path")
    if not p:
        raise InputError("The source file for this job is gone.")
    return p


# ---------- Phase 2: shorts ----------

def clip_words(result: dict, clip: dict, telemetry: Telemetry | None = None) -> list[dict]:
    """
    The clip's words, clip-relative (0 = clip start). Comes from the
    transcript block cache, so it's free when the analysis already
    transcribed this part; otherwise only the clip's own blocks are
    transcribed. [] when transcription is off or fails (no captions).
    """
    s = result["source"]
    cache = StageCache(s["key"])
    if s["kind"] == "file":
        apath = result.get("source_path")
    else:
        found = [os.path.join(cache.dir, n) for n in os.listdir(cache.dir)
                 if n.startswith("audio.") and not n.endswith((".part", ".ytdl", ".tmp"))]
        apath = found[0] if found else None
    if not apath or not os.path.exists(apath):
        return []
    try:
        provider = get_transcriber((result.get("options") or {}).get("transcriber"))
    except ValueError:
        return []
    if provider is None:
        return []
    from recaply.profiles import get_profile
    try:
        vocab = get_profile(result["profile"]["key"]).vocab
    except KeyError:
        vocab = ""
    try:
        words = transcribe_cached(provider, apath, cache, [(clip["start"], clip["end"])],
                                  float(s.get("duration") or clip["end"]), vocab=vocab,
                                  language=(result.get("options") or {}).get("language"), telemetry=telemetry)
    except Exception as e:  # noqa: BLE001
        log.warning("clip transcription failed", error=str(e)[:200])
        return []
    out = []
    for w in words:
        if clip["start"] - 0.05 <= w["s"] < clip["end"]:
            out.append({**w, "s": round(max(0.0, w["s"] - clip["start"]), 3),
                        "e": round(min(clip["end"], w["e"]) - clip["start"], 3)})
    return out


def render_shorts(result: dict, ids: list[int], out_dir: str, opts: dict | None = None, owner=None,
                  watermark: bool = False, cut_files: dict | None = None,
                  progress: Callable[[int, str, float], None] | None = None,
                  telemetry: Telemetry | None = None, words_for: dict | None = None) -> dict[int, dict]:
    """
    Render shorts for these clip ids into out_dir. Clips not cut yet are cut
    first (1080p). `cut_files` maps clip id -> an existing cut file name in
    out_dir. `words_for` maps clip id -> the words of an edited clip (already
    timed to the edited file), used instead of transcribing the original cut.
    Returns {id: render info or {"error"}}.
    """
    from recaply.profiles import get_profile
    from recaply.render.shorts import ShortOptions, render_short
    tel = telemetry or Telemetry()
    o = ShortOptions.from_dict(opts)
    cut_files = dict(cut_files or {})
    missing = [i for i in ids if not (cut_files.get(i) and os.path.exists(os.path.join(out_dir, cut_files[i])))]
    if missing:
        for cid, info in cut_clips(result, missing, out_dir, telemetry=tel).items():
            if "file" in info:
                cut_files[cid] = info["file"]
    try:
        profile = get_profile(result["profile"]["key"])
        reframe_pref, keywords = profile.reframe, profile.keywords
    except KeyError:
        reframe_pref, keywords = "center", ()
    by_id = {c["id"]: c for c in result["clips"]}
    out: dict[int, dict] = {}
    for cid in ids:                               # one at a time: each render already uses the GPU / all cores
        c = by_id.get(cid)
        if c is None:
            continue
        if not cut_files.get(cid):
            out[cid] = {"error": "Cutting the clip failed."}
            continue
        try:
            with tel.stage("words"):
                if words_for and cid in words_for:
                    words = words_for[cid]
                else:
                    words = clip_words(result, c, tel) if (o.captions != "none" or o.tighten or o.broll or o.translate) else []
            with tel.stage("render"):
                info = render_short(c, os.path.join(out_dir, cut_files[cid]), words, out_dir, o,
                                    profile_reframe=reframe_pref, keywords=keywords, owner=owner,
                                    watermark=watermark, telemetry=tel,
                                    progress=(lambda f, s, cid=cid: progress(cid, s, f)) if progress else None)
            out[cid] = {**info, "cut_file": cut_files[cid]}
        except Exception as e:  # noqa: BLE001
            log.error("short failed", clip=cid, error=str(e)[:300])
            out[cid] = {"error": str(e)[:300]}
    return out


# ---------- Phase 5: transcript editing ----------

def edit_clip(result: dict, clip_id: int, deleted: list[int], out_dir: str, cut_file: str | None = None,
              broll: bool = False, telemetry: Telemetry | None = None) -> dict:
    """
    Delete words from a clip and write the edited clip as `edit_<id>.mp4` in
    out_dir (cutting the clip first when `cut_file` is missing). Optional B-roll
    is laid over the edited clip. Returns {"file", "words" (timed to the edited
    clip), "deleted", "duration", "removed_sec", "broll": [...], "notes": [...]}.
    """
    import editor
    from recaply.edit import broll as broll_mod
    from recaply.edit import transcript as tx
    from recaply.providers.llm import get_llm
    tel = telemetry or Telemetry()
    by_id = {c["id"]: c for c in result["clips"]}
    clip = by_id.get(clip_id)
    if clip is None:
        raise InputError("That clip isn't in this job.")
    os.makedirs(out_dir, exist_ok=True)
    if not (cut_file and os.path.exists(os.path.join(out_dir, cut_file))):
        info = cut_clips(result, [clip_id], out_dir, telemetry=tel).get(clip_id) or {}
        if "file" not in info:
            raise RuntimeError(info.get("error") or "Cutting the clip failed.")
        cut_file = info["file"]
    src = os.path.join(out_dir, cut_file)
    with tel.stage("words"):
        words = clip_words(result, clip, tel)
    if not words:
        raise InputError("This clip has no transcript to edit (transcription is off or found no speech).")
    meta = editor.probe(src)
    dele = {int(i) for i in deleted if 0 <= int(i) < len(words)}
    segs = tx.keep_segments(words, dele, meta["duration"])
    dst = os.path.join(out_dir, f"edit_{clip_id}.mp4")
    notes: list[str] = []
    with tel.stage("edit"):
        tx.render(src, dst, segs, has_audio=bool(meta["audio"]))
    new_words = tx.retime(words, dele, segs)
    used: list = []
    if broll and new_words:
        m2 = editor.probe(dst)
        tmp = dst + ".br.mp4"
        with tel.stage("broll"):
            br = broll_mod.add(dst, tmp, new_words, m2["duration"], (m2["w"], m2["h"]), bool(m2["audio"]),
                               os.path.join(out_dir, "_broll", str(clip_id)), get_llm(), tel)
        if br["file"]:
            os.replace(br["file"], dst)
            used = br["inserts"]
        else:
            notes.append(br["note"])
    from recaply.ingest.audio import media_duration
    dur = media_duration(dst)
    return {"file": os.path.basename(dst), "cut_file": cut_file, "words": new_words, "deleted": sorted(dele), "duration": round(dur, 2),
            "removed_sec": round(meta["duration"] - tx.edited_duration(segs), 2), "broll": used, "notes": notes}
