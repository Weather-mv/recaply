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
from recaply.ingest.sources import InputError, Source, resolve
from recaply.log import get_logger
from recaply.profiles import detect_profile, get_profile
from recaply.providers.llm import get_llm
from recaply.providers.transcribe import get_transcriber, transcribe_cached
from recaply.score.boundaries import snap
from recaply.score.fusion import fuse
from recaply.score.rerank import headline, rerank
from recaply.score.select import select
from recaply.score.windows import (peak_windows, sliding_windows, text_score, top_windows,
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


def _noop(stage: str, frac: float) -> None:
    pass


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
        raise InputError("Live streams are clipped by Live mode (coming in Phase 4). "
                         "For a VCT stream, pick the 'Valorant VCT broadcast' profile.")
    if opts.profile in ("", "auto"):
        det = detect_profile(src.meta)
        profile_key, profile_reason = det.profile, f"auto: {det.reason}"
    else:
        profile_key, profile_reason = opts.profile, "chosen"
    try:
        profile = get_profile(profile_key)
    except KeyError:
        raise InputError(f"Unknown profile '{profile_key}'.") from None
    if profile.segmenter:
        raise InputError(f"The '{profile.label}' profile runs in rounds mode in the app.")
    profile = profile.with_lengths(opts.min_len, opts.max_len)
    cache = StageCache(src.key)
    log.info("analyze", source=src.key, title=src.title[:60], profile=profile.key, duration=round(src.duration))

    had_audio = src.kind == "file" or any(n.startswith("audio.") and not n.endswith(".part")
                                          for n in os.listdir(cache.dir))
    progress("Fetching the audio only", 0.02)
    with tel.stage("audio", cached=had_audio):
        apath = audio_ingest.ensure_audio(src, cache, progress=lambda f: progress("Fetching the audio only",
                                                                                   0.02 + 0.2 * f))

    progress("Measuring loudness", 0.22)
    hit = cache.get_array("rms", {"v": 1})
    with tel.stage("loudness", cached=hit is not None):
        rms = hit if hit is not None else cache.array("rms", {"v": 1}, lambda: audio_ingest.loudness(
            apath, src.duration, workers=settings.workers,
            progress=lambda f: progress("Measuring loudness", 0.22 + 0.1 * f)))
    duration = float(src.duration or len(rms))
    n = len(rms)

    # ---- analyze: cheap signals over the whole video
    progress("Analysing signals", 0.32)
    ctx = SignalContext(source=src, profile=profile, cache=cache, audio_path=apath, duration=duration, rms=rms)
    sigs: dict[str, np.ndarray] = {}
    with tel.stage("signals"):
        for name, weight in profile.weights.items():
            plugin = get_signal(name)
            if weight <= 0 or plugin is None:
                continue
            try:
                if not plugin.available(ctx):
                    continue
                sigs[name] = fit_length(cache.array(f"sig-{name}", {"v": plugin.version},
                                                    lambda p=plugin: p.compute(ctx)), n)
            except Exception as e:  # noqa: BLE001 — fall back to the other signals
                log.warning("signal failed", signal=name, error=str(e)[:200])
                tel.note(f"signal {name} skipped: {str(e)[:120]}")
    missing = [k for k in profile.weights if k not in sigs and k != "transcript"]
    if missing:
        tel.note("signals not available yet: " + ", ".join(missing))
    fused = fuse(sigs, profile.weights, n, dead=dead_air(rms))

    # ---- shortlist (+ transcripts)
    k = opts.shortlist or settings.shortlist_size or (24 if profile.kind == "talk" else 16)
    transcriber = None
    try:
        transcriber = get_transcriber(opts.transcriber)
    except ValueError as e:
        tel.note(f"transcription off: {e}")
    text_w = profile.weights.get("transcript", 0.0)
    words: list[dict] = []
    vocab = profile.vocab

    def do_transcribe(spans, label, base, span):
        nonlocal transcriber
        if transcriber is None:
            return []
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
        words = do_transcribe([(0.0, duration)], "Transcribing", 0.34, 0.4)
        windows = sliding_windows(fused, profile, duration)
        for w in windows:
            w.text_score = text_score(words, profile, w.start, w.end)
        windows = top_windows(windows, k, text_w if words else 0.0)
        for w in windows:
            w.words = words_between(words, w.start - 10, w.end + 10)
    else:
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
    if llm is None:
        tel.note(f"no LLM configured ({opts.llm or settings.llm_provider}); heuristic scores")
    progress("Scoring with AI" if llm else "Scoring", 0.76)
    with tel.stage("score"):
        suggestions, scorer = rerank(windows, profile, src.title, llm, tel)

    # ---- select: clean boundaries, de-duplicate, rank
    db = to_db(rms)
    clips = []
    for i, (w, sug) in enumerate(zip(windows, suggestions), 1):
        s, e = snap(w.words, sug["start"], sug["end"], profile.clip_min, profile.clip_max, duration, energy=db)
        cw = words_between(w.words, s, e)
        if sug.get("llm_score") is None:           # rule-based: title from the final clip's best line
            sug["title"] = headline(cw, profile) or f"Moment at {fmt_hms(w.peak)}"
        clips.append({
            "id": i, "start": s, "end": e, "duration": round(e - s, 2), "time": fmt_hms(s),
            "score": sug["score"], "llm_score": sug.get("llm_score"),
            "signal_score": round(100 * w.signal_score, 1), "text_score": round(100 * w.text_score, 1),
            "reason": sug["reason"], "title": sug["title"], "hook": sug["hook"],
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
        "clips": chosen, "shortlist": len(windows), "options": asdict(opts),
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
                  telemetry: Telemetry | None = None) -> dict[int, dict]:
    """
    Render shorts for these clip ids into out_dir. Clips not cut yet are cut
    first (1080p). `cut_files` maps clip id -> an existing cut file name in
    out_dir. Returns {id: render info or {"error"}}.
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
                words = clip_words(result, c, tel) if o.captions != "none" or o.tighten else []
            with tel.stage("render"):
                info = render_short(c, os.path.join(out_dir, cut_files[cid]), words, out_dir, o,
                                    profile_reframe=reframe_pref, keywords=keywords, owner=owner,
                                    watermark=watermark,
                                    progress=(lambda f, s, cid=cid: progress(cid, s, f)) if progress else None)
            out[cid] = {**info, "cut_file": cut_files[cid]}
        except Exception as e:  # noqa: BLE001
            log.error("short failed", clip=cid, error=str(e)[:300])
            out[cid] = {"error": str(e)[:300]}
    return out
