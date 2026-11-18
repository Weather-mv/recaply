"""
Command line:

    python -m recaply clip <link-or-file> [--profile auto] [--count 10]
                           [--min 15] [--max 60] [--cut] [--out DIR]
                           [--transcriber faster-whisper|groq|openai|none]
                           [--llm gemini|anthropic|openai|ollama|none] [--json FILE]
    python -m recaply profiles

Runs the pipeline in this process (no queue), prints the ranked clips and
the per-stage timing/cost summary, and with --cut writes the clips.
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
                   shortlist=args.shortlist)
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
    print(f"\ndone in {time.time() - t0:.1f}s")
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
    c.add_argument("--cut", action="store_true", help="also cut the clips")
    c.add_argument("--quality", default="1080", choices=("1080", "720", "best"))
    c.add_argument("--out", default=None)
    c.add_argument("--json", default=None, help="write the full result as JSON")
    c.set_defaults(fn=cmd_clip)
    p = sub.add_parser("profiles", help="list content profiles")
    p.set_defaults(fn=cmd_profiles)
    args = ap.parse_args(argv)
    return args.fn(args)
