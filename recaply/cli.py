"""
Command line:

    python -m recaply clip <link-or-file> [--profile auto] [--count 10]
                           [--min 15] [--max 60] [--cut] [--out DIR]
                           [--shorts [--export shorts|tiktok|reels|portrait|square|landscape|draft]
                            [--layout auto|face|split|action|center|fit] [--captions bold|karaoke|none|...]
                            [--tighten] [--emoji] [--no-hook] [--no-brand]]
                           [--transcriber faster-whisper|groq|openai|none]
                           [--llm gemini|anthropic|openai|ollama|none] [--json FILE]
    python -m recaply bench <link-or-file> [--profile auto] [--transcriber ...] [--warm]
    python -m recaply live add NAME URL [--profile gaming] [--pre 30] [--post 15] [--threshold 0.5]
                               [--discord URL] [--webhook URL] [--shorts] [--no-chat] [--auto-start]
    python -m recaply live run [NAME-or-URL ...]      (no names: every saved channel; Ctrl-C stops)
    python -m recaply live list | remove NAME | events [--channel NAME] [--follow]
    python -m recaply batch LINKS.txt [--profile auto] [--count 10]      (one link per line; waits for all)
    python -m recaply usage [--days 30]                                  (time and estimated cost)
    python -m recaply publish status | auth youtube | disconnect youtube
    python -m recaply publish add JOB FILE.mp4 [--platform youtube] [--title T] [--desc D] [--tags "a b"]
                                           [--at 2026-11-20T09:00] [--privacy public|unlisted|private]
    python -m recaply publish list | run | cancel ID | retry ID           (run = upload what's due, until Ctrl-C)
    python -m recaply profiles

Runs the pipeline in this process (no queue), prints the ranked clips and
the per-stage timing/cost summary, with --cut writes the clips, and with
--shorts renders them as finished vertical shorts (reframed, captioned, with a
hook) and writes posts.json with each clip's title, description and hashtags.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from common import fmt_hms


def _bar(stage: str, frac: float, state={"last": ""}):  # noqa: B006 — tiny progress line
    line = f"\r{stage:<36} {int(frac * 100):3d}%"
    if line != state["last"]:
        sys.stderr.write(line)
        sys.stderr.flush()
        state["last"] = line


def cmd_profiles(_args) -> int:
    from recaply.profiles import all_profiles
    for p in all_profiles():
        w = ", ".join(f"{k} {v:.2f}" for k, v in p.weights.items())
        print(f"{p.key:<14} {p.label:<40} {p.clip_min:.0f}-{p.clip_max:.0f}s  [{w}]")
    return 0


def cmd_clip(args) -> int:
    from recaply.engine import Options, analyze, cut_clips
    from recaply.ingest.sources import InputError
    opts = Options(profile=args.profile, count=args.count, min_len=args.min, max_len=args.max,
                   language=args.language, transcriber=args.transcriber, llm=args.llm,
                   shortlist=args.shortlist, chat_file=args.chat)
    t0 = time.time()
    try:
        result = analyze(args.input, opts, progress=_bar)
    except InputError as e:
        sys.stderr.write(f"\n{e}\n")
        return 2
    except Exception as e:  # noqa: BLE001 — platform errors (age gate, removed video, bot check)
        from common import friendly_error
        sys.stderr.write(f"\n{friendly_error(e)}\n")
        return 1
    sys.stderr.write("\n")
    src, prof = result["source"], result["profile"]
    print(f"\n{src['title']}  ({fmt_hms(src['duration'])})")
    print(f"profile: {prof['label']} ({prof['reason']}); scorer: {result['scorer']}; "
          f"transcriber: {result['transcriber']}; shortlist: {result['shortlist']}\n")
    for c in result["clips"]:
        print(f"#{c['rank']:<2} {c['score']:5.1f}  {fmt_hms(c['start'])}-{fmt_hms(c['end'])} "
              f"({c['duration']:.0f}s)  {c['title']}")
        if c["reason"]:
            print(f"      {c['reason']}")
        if c["hook"]:
            print(f"      hook: {c['hook']}")
    s = result["summary"]
    fn = result.get("funnel") or {}
    print(f"signals: {', '.join(result.get('signals_used') or []) or 'none'}"
          + (f"; chat: {result['chat_messages']} messages" if result.get("chat_messages") else ""))
    if fn.get("audio_sec"):
        print(f"funnel: transcribed {fn['transcribed_sec']:.0f}s of {fn['audio_sec']:.0f}s of audio "
              f"({fn['share']:.0%}), {fn['llm_calls']} AI calls")
    print("\nstages: " + ", ".join(f"{k} {v['sec']}s{' (cached)' if v['cached'] else ''}"
                                   for k, v in s["stages"].items()))
    if s["tokens"]:
        print("tokens: " + ", ".join(f"{k} {v['in']} in / {v['out']} out" for k, v in s["tokens"].items()))
    print(f"total {s['total_sec']}s, est. cost ${s['est_usd']:.4f}")
    for n in s["notes"]:
        print(f"note: {n}")
    if src["duration"]:
        print(f"speed: {s['total_sec'] / (src['duration'] / 3600):.0f} s per hour of video")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1)
    if args.cut and result["clips"]:
        out_dir = args.out or os.path.join("clips_out", result["source"]["key"])
        sys.stderr.write(f"Cutting {len(result['clips'])} clips into {out_dir} ...\n")
        out = cut_clips(result, [c["id"] for c in result["clips"]], out_dir, quality=args.quality)
        for cid, info in sorted(out.items()):
            print(f"  clip {cid}: {info.get('file') or 'FAILED ' + info.get('error', '')}")
    if args.shorts and result["clips"]:
        out_dir = args.out or os.path.join("clips_out", result["source"]["key"])
        rc = _shorts(args, result, out_dir)
        if rc:
            return rc
    print(f"\ndone in {time.time() - t0:.1f}s")
    return 0


def _shorts(args, result: dict, out_dir: str) -> int:
    from recaply.engine import render_shorts
    ids = [c["id"] for c in result["clips"]]
    opts = {"export": args.export, "layout": args.layout, "captions": args.captions, "hook": not args.no_hook,
            "emoji": args.emoji, "tighten": args.tighten, "use_brand": not args.no_brand}
    sys.stderr.write(f"Rendering {len(ids)} shorts into {out_dir} ...\n")
    done = {"n": 0}

    def progress(cid, label, frac):
        _bar(f"short {cid}: {label}", frac)
        if frac >= 1.0:
            done["n"] += 1

    out = render_shorts(result, ids, out_dir, opts, owner="local", progress=progress)
    sys.stderr.write("\n")
    posts, failed = [], 0
    by_id = {c["id"]: c for c in result["clips"]}
    for cid, info in sorted(out.items()):
        if "error" in info:
            failed += 1
            print(f"  short {cid}: FAILED {info['error']}")
            continue
        c = by_id[cid]
        print(f"  short {cid}: {info['file']}  ({info['duration']:.0f}s, {info['layout']}: {info['reason']})")
        for n in info.get("notes") or []:
            print(f"      note: {n}")
        posts.append({"file": info["file"], "title": c["title"], "description": c.get("description") or "",
                      "hashtags": c.get("hashtags") or [], "hook": c.get("hook") or "", "score": c["score"]})
    if posts:
        with open(os.path.join(out_dir, "posts.json"), "w", encoding="utf-8") as f:
            json.dump(posts, f, indent=1, ensure_ascii=False)
        print(f"  post titles, descriptions and hashtags: {os.path.join(out_dir, 'posts.json')}")
    return 1 if failed and not posts else 0


def cmd_bench(args) -> int:
    """Run the funnel once from a cold cache and report what it cost: seconds per hour of video, share transcribed."""
    import shutil

    from recaply.cache import StageCache
    from recaply.engine import Options, analyze
    from recaply.ingest.sources import InputError, resolve
    from recaply.settings import settings
    try:
        src = resolve(args.input)
        if not args.warm:
            shutil.rmtree(StageCache(src.key).dir, ignore_errors=True)
        res = analyze(src, Options(profile=args.profile, count=args.count, transcriber=args.transcriber,
                                   llm=args.llm, chat_file=args.chat), progress=lambda *a: None)
    except InputError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    s, fn, dur = res["summary"], res["funnel"], res["source"]["duration"] or 0.0
    hours = max(dur / 3600.0, 1e-9)
    print(f"{res['source']['title']}  ({fmt_hms(dur)})  profile {res['profile']['key']}  "
          f"{'warm' if args.warm else 'cold'} cache; signals: {', '.join(res['signals_used'])}")
    print(f"\n{'stage':<24}{'seconds':>9}{'s per hour of video':>22}")
    for k, v in s["stages"].items():
        if k == "signals":
            continue
        print(f"{k:<24}{v['sec']:>9.1f}{v['sec'] / hours:>22.1f}{'  (cached)' if v['cached'] else ''}")
    print(f"{'TOTAL':<24}{s['total_sec']:>9.1f}{s['total_sec'] / hours:>22.1f}")
    print(f"\nfunnel: {fn['windows']} windows shortlisted; transcribed {fn['transcribed_sec']:.0f}s of "
          f"{fn['audio_sec']:.0f}s of audio ({fn['share']:.0%}); {fn['llm_calls']} AI calls")
    tr = res.get("transcriber") or ""
    price = settings.prices.audio_hour.get("groq" if "groq" in tr else "openai" if "openai" in tr else "", 0.0)
    full = dur / 3600.0 * price
    spent = fn["transcribed_sec"] / 3600.0 * price
    if price:
        print(f"transcription cost: ${spent:.4f} with the funnel vs ${full:.4f} for the whole audio "
              f"({(1 - spent / full):.0%} saved)" if full else "")
    t_tr = s["stages"].get("transcribe", {}).get("sec")
    if t_tr and fn["transcribed_sec"]:
        rtf = t_tr / fn["transcribed_sec"]
        print(f"transcription speed: {rtf:.3f} s per audio second here; whole audio would take about "
              f"{rtf * dur:.0f}s (extrapolated) instead of {t_tr:.0f}s measured")
    print(f"AI: est. ${s['est_usd']:.4f}")
    for n in s["notes"]:
        print(f"note: {n}")
    return 0


def _live_cfg_from(args):
    from recaply.live import config as lcfg
    d = {"name": args.name, "url": args.url, "profile": args.profile, "chat": not args.no_chat,
         "auto_shorts": args.shorts, "enabled": args.auto_start, "discord": args.discord or "",
         "webhook": args.webhook or ""}
    for k in ("pre", "post", "threshold", "cooldown", "warmup", "count", "retention_min", "end_grace", "poll_sec", "max_hours"):
        v = getattr(args, k, None)
        if v is not None:
            d[k] = v
    return lcfg.validate(d)


def _print_event(e: dict) -> None:
    clock = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
    line = f"{clock}  [{e['channel']}] {e.get('title') or e['kind']}"
    if e.get("text") and e["kind"] != "spike":
        line += f" - {e['text']}"
    elif e["kind"] == "spike":
        line += f" (score {e.get('score')})"
    print(line, flush=True)


def cmd_live(args) -> int:
    from recaply.live import config as lcfg
    from recaply.live import events
    from recaply.live.config import ConfigError
    from recaply.live.manager import LiveManager
    a = args.live_cmd
    try:
        if a == "add":
            cfg = _live_cfg_from(args)
            lcfg.save(cfg)
            print(f"saved {cfg.name}. Start it with: python -m recaply live run {cfg.name}")
            return 0
        if a == "list":
            chs = lcfg.load_all()
            for c in chs.values():
                print(f"{c.name:<16} {c.profile:<10} -{c.pre:.0f}s/+{c.post:.0f}s  threshold {c.threshold:g}  "
                      f"{'auto-start  ' if c.enabled else ''}{c.url}")
            if not chs:
                print("no channels yet: python -m recaply live add NAME URL")
            return 0
        if a == "remove":
            print("removed" if lcfg.remove(args.name) else "no such channel")
            return 0
        if a == "events":
            last = 0
            while True:
                for e in events.list_events(since_id=last, channel=args.channel, limit=200):
                    last = max(last, e["id"])
                    _print_event(e)
                if not args.follow:
                    return 0
                time.sleep(2)
        if a == "run":
            return _live_run(args, lcfg, events, LiveManager, ConfigError)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


def _live_run(args, lcfg, events, LiveManager, ConfigError) -> int:
    import re
    import threading
    mgr = LiveManager()
    cfgs = []
    saved = lcfg.load_all()
    if not args.targets:
        cfgs = [c for c in saved.values()]
        if not cfgs:
            print("no saved channels. Add one: python -m recaply live add NAME URL, or run a link directly.", file=sys.stderr)
            return 2
    for t in args.targets:
        if t in saved:
            cfgs.append(saved[t])
        elif t.lower().startswith(("http://", "https://")):
            from urllib.parse import urlparse
            parts = [p for p in urlparse(t).path.split("/") if p]
            name = re.sub(r"[^a-z0-9_-]+", "", (parts[0] if parts else urlparse(t).hostname or "live").lower().lstrip("@"))[:40] or "live"
            ns = argparse.Namespace(name=name, url=t, profile=args.profile, no_chat=args.no_chat, shorts=args.shorts,
                                    auto_start=False, discord=args.discord, webhook=args.webhook,
                                    **{k: getattr(args, k, None) for k in ("pre", "post", "threshold", "cooldown", "warmup",
                                                                          "count", "retention_min", "end_grace", "poll_sec", "max_hours")})
            cfgs.append(_live_cfg_from(ns))
        else:
            raise ConfigError(f"'{t}' is neither a saved channel nor a link.")
    for c in cfgs:
        mgr.start(c)
        print(f"watching {c.name} ({c.url}). Ctrl-C to stop.", flush=True)
    last = max([e["id"] for e in events.list_events(limit=1)] or [0])
    try:
        while any(mgr.running(c.name) for c in cfgs):
            for e in events.list_events(since_id=last, limit=200):
                last = max(last, e["id"])
                _print_event(e)
            time.sleep(1.5)
    except KeyboardInterrupt:
        print("\nstopping: cutting any moment still waiting...", flush=True)
        mgr.stop_all()
    pending = [t for t in threading.enumerate() if t.name.startswith("live-follow-")]
    if pending:
        print(f"waiting for {len(pending)} clip(s) to finish ranking (Ctrl-C to leave them)...", flush=True)
        try:
            while any(t.is_alive() for t in pending):
                time.sleep(1.5)
        except KeyboardInterrupt:
            pass
    for e in events.list_events(since_id=last, limit=200):
        _print_event(e)
    return 0


def cmd_usage(args) -> int:
    from recaply import usage
    from recaply.jobs import store
    store.init()
    d = usage.collect(everyone=True, days=args.days)
    t = d["totals"]
    print(f"last {d['days']} days: {t['jobs']} videos ({t['failed']} failed), {t['video_hours']} h of video, {t['clips']} clips")
    if t["sec_per_video_hour"] is not None:
        print(f"processing: {t['sec_per_video_hour']:.0f} s per hour of video")
    print(f"estimated spend: ${t['usd']:.2f}" + (f"  (${t['usd_per_video']:.3f} per video)" if t["usd_per_video"] is not None else ""))
    for name, v in d["providers"].items():
        print(f"  {name:<22} ${v['usd']:.3f}  " + (f"{v['minutes']} min" if v["kind"] == "audio" else f"{v['calls']} calls, {v['tokens_in'] + v['tokens_out']:,} tokens"))
    for st in d["stages"][:8]:
        print(f"  stage {st['name']:<14} {st['sec']:>8.1f} s  {st['share'] * 100:4.0f}%")
    return 0


def cmd_batch(args) -> int:
    from recaply import batch
    from recaply.jobs import store
    from recaply.jobs.queue import get_queue
    get_queue()
    try:
        with open(args.file, encoding="utf-8") as f:
            lines = batch.parse_inputs(f.read())
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    opts = {"profile": args.profile, "count": args.count, "allow_local": True}
    res = batch.start_batch(lines, opts, allow_local=True)
    ids = [r["job"] for r in res if "job" in r]
    for r in res:
        print(("started " + r["job"]) if "job" in r else f"skipped  {r['input'][:70]}: {r['error']}", flush=True)
    while ids:
        states = {i: store.get(i) for i in ids}
        busy = [i for i, j in states.items() if j and j["state"] in ("queued", "running")]
        sys.stderr.write(f"\r{len(ids) - len(busy)}/{len(ids)} done   ")
        sys.stderr.flush()
        if not busy:
            break
        time.sleep(2)
    sys.stderr.write("\n")
    for i in ids:
        j = store.get(i)
        print(f"{i}  {j['state']:<7} {j['title'][:60]}" + (f"  ({j['error'][:80]})" if j.get("error") else ""))
    return 0 if all((store.get(i) or {}).get("state") == "ready" for i in ids) else 1


def cmd_publish(args) -> int:
    from recaply import publish
    from recaply.publish import queue as pq
    from recaply.publish.base import PublishError
    a = args.pub_cmd
    try:
        if a == "status":
            for p in publish.overview():
                print(f"{p['key']:<10} {'connected' if p['connected'] else 'not connected':<14} {p['note']}")
            return 0
        if a in ("auth", "disconnect"):
            if args.platform != "youtube":
                print("only youtube needs signing in", file=sys.stderr)
                return 2
            from recaply.publish import youtube
            if a == "disconnect":
                print("disconnected" if youtube.disconnect() else "wasn't connected")
                return 0
            youtube.YouTubePublisher().authorize()
            print("connected to YouTube")
            return 0
        if a == "add":
            from datetime import datetime
            when = datetime.fromisoformat(args.at).timestamp() if args.at else None
            n = pq.enqueue(args.job, args.file, args.platform, args.title or os.path.splitext(args.file)[0],
                           args.desc or "", (args.tags or "").replace(",", " ").split(), when, args.privacy)
            print(f"queued #{n} for {args.platform}" + (f" at {args.at}" if args.at else " (now): run `publish run` to upload"))
            return 0
        if a == "list":
            for it in pq.list_items(limit=50):
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(it["run_at"]))
                print(f"#{it['id']:<4} {it['status']:<10} {when}  {it['platform']:<8} {it['title'][:40]}"
                      + (f"  {it['result']['url']}" if it.get("result") and it["result"].get("url") else "")
                      + (f"  ({it['error'][:60]})" if it.get("error") else ""))
            return 0
        if a == "cancel":
            print("cancelled" if pq.cancel(args.id) else "can't cancel that one")
            return 0
        if a == "retry":
            print("queued again" if pq.retry(args.id) else "can't retry that one")
            return 0
        if a == "run":
            pq.reset_stuck()
            print("uploading what is due, every 15 s. Ctrl-C to stop.", flush=True)
            try:
                while True:
                    n = pq.run_due()
                    if n:
                        for it in pq.list_items(limit=n):
                            print(f"#{it['id']} {it['status']} {it['title'][:40]} {(it.get('result') or {}).get('url', '')} {it.get('error') or ''}", flush=True)
                    time.sleep(15)
            except KeyboardInterrupt:
                return 0
    except PublishError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


def cmd_worker(args) -> int:
    from recaply.jobs.queue import run_worker
    try:
        return run_worker(burst=args.burst)
    except Exception as e:  # noqa: BLE001
        print(f"Can't start the worker: {e}")
        print("Needs: pip install rq redis, a running Redis, and REDIS_URL.")
        return 2


def cmd_status(args) -> int:
    from recaply.jobs.queue import queue_info
    from recaply.providers.llm import ai_status, ollama_status
    from recaply.settings import settings
    st = ollama_status()
    ai = ai_status()
    print(f"LLM provider : {settings.llm_provider} | order: {' -> '.join(ai['order'] + ['rules']) }")
    if settings.llm_provider in ("ollama", "auto"):
        print(f"Ollama       : {'running' if st['running'] else 'NOT running'}"
              f" | model {st['model']} {'ready' if st['model_ready'] else 'missing'}")
        if st["hint"]:
            print(f"               {st['hint']}")
    q = queue_info()
    print(f"Job queue    : {q['backend']} | {q['waiting']} waiting | {q['workers']} worker(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="recaply", description="Find, score and cut short clips from long videos.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clip", help="find clips in a link or a local file")
    c.add_argument("input", help="YouTube / Twitch / Kick link, or a local video file")
    c.add_argument("--profile", default="auto", help="auto, talk, gaming, fps, open_world, irl, valorant")
    c.add_argument("--count", type=int, default=10)
    c.add_argument("--min", type=float, default=None, help="shortest clip (s)")
    c.add_argument("--max", type=float, default=None, help="longest clip (s)")
    c.add_argument("--language", default=None)
    c.add_argument("--transcriber", default=None)
    c.add_argument("--llm", default=None)
    c.add_argument("--shortlist", type=int, default=None)
    c.add_argument("--chat", default=None, help="chat export for a local file (TwitchDownloader JSON, yt-dlp live_chat, or [{t, text}])")
    c.add_argument("--cut", action="store_true", help="also cut the clips")
    c.add_argument("--quality", default="1080", choices=("1080", "720", "best"))
    c.add_argument("--out", default=None)
    c.add_argument("--shorts", action="store_true", help="render finished vertical shorts (implies cutting)")
    c.add_argument("--export", default="shorts", help="export preset (shorts, tiktok, reels, portrait, square, landscape, draft)")
    c.add_argument("--layout", default="auto", help="auto, face, split, action, center, fit")
    c.add_argument("--captions", default="bold", help="caption style (bold, box, single, neon, clean, subtitle, karaoke, punch, marker, none)")
    c.add_argument("--tighten", action="store_true", help="remove filler words and long pauses")
    c.add_argument("--emoji", action="store_true", help="add emoji to captions")
    c.add_argument("--no-hook", action="store_true", help="no on-screen hook text")
    c.add_argument("--no-brand", action="store_true", help="ignore the saved brand kit")
    c.add_argument("--json", default=None, help="write the full result as JSON")
    c.set_defaults(fn=cmd_clip)
    b = sub.add_parser("bench", help="measure the funnel on one video (cold cache)")
    b.add_argument("input")
    b.add_argument("--profile", default="auto")
    b.add_argument("--count", type=int, default=10)
    b.add_argument("--transcriber", default=None)
    b.add_argument("--llm", default=None)
    b.add_argument("--chat", default=None)
    b.add_argument("--warm", action="store_true", help="keep the cache (measure a re-run)")
    b.set_defaults(fn=cmd_bench)
    lv = sub.add_parser("live", help="live-stream mode: watch channels and clip moments as they happen")
    ls = lv.add_subparsers(dest="live_cmd", required=True)

    def live_opts(q):
        q.add_argument("--profile", default="gaming")
        q.add_argument("--pre", type=float, default=None, help="seconds before the moment (default 30)")
        q.add_argument("--post", type=float, default=None, help="seconds after the moment (default 15)")
        q.add_argument("--threshold", type=float, default=None, help="0.15-0.95, lower = more clips (default 0.5)")
        q.add_argument("--cooldown", type=float, default=None, help="min seconds between clips (default 60)")
        q.add_argument("--warmup", type=float, default=None)
        q.add_argument("--count", type=int, default=None, help="clips kept per captured moment")
        q.add_argument("--retention-min", dest="retention_min", type=int, default=None)
        q.add_argument("--end-grace", dest="end_grace", type=int, default=None, help="seconds offline before it counts as ended")
        q.add_argument("--poll-sec", dest="poll_sec", type=int, default=None)
        q.add_argument("--max-hours", dest="max_hours", type=float, default=None)
        q.add_argument("--discord", default=None, help="Discord webhook URL")
        q.add_argument("--webhook", default=None, help="generic JSON webhook URL")
        q.add_argument("--shorts", action="store_true", help="also render the best clip as a short")
        q.add_argument("--no-chat", action="store_true")

    la = ls.add_parser("add", help="save a channel")
    la.add_argument("name")
    la.add_argument("url")
    live_opts(la)
    la.add_argument("--auto-start", action="store_true", help="watch it whenever the app starts")
    lr = ls.add_parser("run", help="watch saved channels (or links) until they end")
    lr.add_argument("targets", nargs="*", help="saved channel names or links; none = all saved")
    live_opts(lr)
    ls.add_parser("list", help="saved channels")
    lm = ls.add_parser("remove", help="forget a channel")
    lm.add_argument("name")
    le = ls.add_parser("events", help="spikes, clips and notifications so far")
    le.add_argument("--channel", default=None)
    le.add_argument("--follow", action="store_true")
    lv.set_defaults(fn=cmd_live)
    u = sub.add_parser("usage", help="time and estimated cost of the jobs so far")
    u.add_argument("--days", type=int, default=30)
    u.set_defaults(fn=cmd_usage)
    bt = sub.add_parser("batch", help="analyse many videos from a file of links (one per line)")
    bt.add_argument("file")
    bt.add_argument("--profile", default="auto")
    bt.add_argument("--count", type=int, default=10)
    bt.set_defaults(fn=cmd_batch)
    pb = sub.add_parser("publish", help="publish / schedule finished clips")
    ps = pb.add_subparsers(dest="pub_cmd", required=True)
    ps.add_parser("status", help="which platforms are connected")
    for name in ("auth", "disconnect"):
        q = ps.add_parser(name)
        q.add_argument("platform", choices=("youtube",))
    pa = ps.add_parser("add", help="queue a finished clip (JOB is a job id, FILE a file of that job)")
    pa.add_argument("job")
    pa.add_argument("file")
    pa.add_argument("--platform", default="youtube")
    pa.add_argument("--title", default=None)
    pa.add_argument("--desc", default=None)
    pa.add_argument("--tags", default=None)
    pa.add_argument("--at", default=None, help="local time, e.g. 2026-11-20T09:00 (default: now)")
    pa.add_argument("--privacy", default="public", choices=("public", "unlisted", "private"))
    ps.add_parser("list")
    ps.add_parser("run", help="upload what is due, until Ctrl-C")
    for name in ("cancel", "retry"):
        q = ps.add_parser(name)
        q.add_argument("id", type=int)
    pb.set_defaults(fn=cmd_publish)
    p = sub.add_parser("profiles", help="list content profiles")
    p.set_defaults(fn=cmd_profiles)
    wk = sub.add_parser("worker", help="run a job worker for the Redis queue (RECAPLY_QUEUE=rq)")
    wk.add_argument("--burst", action="store_true", help="stop when the queue is empty")
    wk.set_defaults(fn=cmd_worker)
    st = sub.add_parser("status", help="is the local AI (Ollama) and the job queue ready?")
    st.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)
