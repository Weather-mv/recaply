"""
Usage and cost dashboard data: what the jobs cost in time and (estimated)
money, from the per-stage telemetry every job already stores.

Money is an estimate from list prices (`settings.prices`, overridable):
LLM tokens and cloud transcription minutes. Local faster-whisper and Ollama
cost nothing but time. Seconds per video hour is processing time divided by
the length of the videos analysed.
"""

from __future__ import annotations

import time

from recaply.jobs import store
from recaply.settings import settings


def _job_usd(summary: dict) -> float:
    if "est_usd" in summary:
        return float(summary["est_usd"] or 0)
    return 0.0


def _provider_usd(tokens: dict, audio_min: dict) -> dict[str, dict]:
    p = settings.prices
    out: dict[str, dict] = {}
    for prov, t in (tokens or {}).items():
        usd = t.get("in", 0) / 1e6 * p.llm_in.get(prov, 0.0) + t.get("out", 0) / 1e6 * p.llm_out.get(prov, 0.0)
        out[prov] = {"kind": "llm", "calls": t.get("calls", 0), "tokens_in": t.get("in", 0), "tokens_out": t.get("out", 0), "usd": usd}
    for prov, m in (audio_min or {}).items():
        out[f"{prov} (audio)"] = {"kind": "audio", "minutes": m, "usd": m / 60 * p.audio_hour.get(prov, 0.0)}
    return out


def collect(owner=None, everyone: bool = False, days: int = 30) -> dict:
    """The dashboard numbers for the last `days` days."""
    since = time.time() - days * 86400
    rows = store.usage_rows(owner=owner, everyone=everyone, since=since)
    jobs, by_day, providers, stages = [], {}, {}, {}
    tot = {"jobs": 0, "ready": 0, "failed": 0, "video_sec": 0.0, "processing_sec": 0.0, "usd": 0.0, "clips": 0}
    for r in rows:
        s = r["summary"] or {}
        usd, proc = _job_usd(s), float(s.get("total_sec") or 0)
        tot["jobs"] += 1
        tot["ready"] += r["state"] == "ready"
        tot["failed"] += r["state"] == "failed"
        tot["clips"] += r["clips"]
        tot["usd"] += usd
        if r["state"] == "ready":
            tot["video_sec"] += r["duration"]
            tot["processing_sec"] += proc
        day = time.strftime("%Y-%m-%d", time.localtime(r["created"]))
        d = by_day.setdefault(day, {"day": day, "jobs": 0, "usd": 0.0, "video_min": 0.0})
        d["jobs"] += 1
        d["usd"] += usd
        d["video_min"] += r["duration"] / 60
        for name, v in _provider_usd(s.get("tokens"), s.get("audio_min")).items():
            a = providers.setdefault(name, {"kind": v["kind"], "usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "minutes": 0.0})
            a["usd"] += v["usd"]
            for k in ("calls", "tokens_in", "tokens_out", "minutes"):
                a[k] += v.get(k, 0)
        for group in ("stages", "cut", "edit", "shorts"):
            for name, v in (s.get(group) or {}).items():
                if not isinstance(v, dict):
                    continue
                a = stages.setdefault(name, {"sec": 0.0, "runs": 0, "cached": 0})
                a["sec"] += float(v.get("sec", 0))
                a["runs"] += int(v.get("runs", 0))
                a["cached"] += 1 if v.get("cached") else 0
        jobs.append({"id": r["id"], "title": r["title"] or r["input"][:80], "created": r["created"], "state": r["state"],
                     "video_min": round(r["duration"] / 60, 1), "processing_sec": round(proc, 1), "usd": round(usd, 4),
                     "clips": r["clips"]})
    hours = tot["video_sec"] / 3600
    total_stage = sum(v["sec"] for v in stages.values()) or 1.0
    ready = tot["ready"]
    return {
        "days": days,
        "totals": {**tot, "usd": round(tot["usd"], 4), "video_hours": round(hours, 2),
                   "sec_per_video_hour": round(tot["processing_sec"] / hours, 1) if hours > 0 else None,
                   "usd_per_video": round(tot["usd"] / ready, 4) if ready else None,
                   "usd_per_video_hour": round(tot["usd"] / hours, 4) if hours > 0 else None},
        "by_day": sorted(({**v, "usd": round(v["usd"], 4), "video_min": round(v["video_min"], 1)} for v in by_day.values()),
                         key=lambda x: x["day"]),
        "providers": {k: {**v, "usd": round(v["usd"], 4), "minutes": round(v["minutes"], 1)} for k, v in providers.items()},
        "stages": sorted(({"name": k, "sec": round(v["sec"], 1), "runs": v["runs"], "cached": v["cached"],
                           "share": round(v["sec"] / total_stage, 3)} for k, v in stages.items()), key=lambda x: -x["sec"]),
        "jobs": jobs[:100],
        "prices_note": "Estimates from list prices; local Whisper and Ollama are free.",
    }


def publish_counts() -> dict:
    from recaply.publish import queue as pq
    out: dict[str, int] = {}
    for it in pq.list_items(limit=1000):
        out[it["status"]] = out.get(it["status"], 0) + 1
    return out
