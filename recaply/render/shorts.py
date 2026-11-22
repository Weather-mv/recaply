"""
Turn a cut clip into a finished short:

    clip.mp4 -> [tighten: drop fillers / long pauses] -> reframe plan ->
    editor document (video layers + karaoke captions + hook + logo) ->
    editor.render (one ffmpeg pass, GPU encode) -> [+ intro / outro]

The document is saved next to the short with the source it was rendered
from, so the short can be opened in the editor and adjusted by hand.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from typing import Callable

import editor
from recaply.render import brand as brandkit
from recaply.render import reframe, subtitles, tighten
from recaply.render.presets import export_preset, length_note, ratio


@dataclass
class ShortOptions:
    export: str = "shorts"            # recaply.render.presets.EXPORTS key
    layout: str = "auto"              # auto | face | split | action | center | fit
    captions: str = "bold"            # a subtitles.PRESETS key, or "none"
    hook: bool = True
    emoji: bool = False
    tighten: bool = False             # remove fillers and long pauses
    use_brand: bool = True
    broll: bool = False               # lay stock cut-aways over the clip (Phase 5)
    translate: str = ""               # translate the captions to this language code ("" = no)
    teaser: bool = False              # a 3 s cinematic cold open in front of the short (the studio turns it on)
    bgm: str = ""                     # "" none | "auto" | a file name in assets/bgm
    bgm_level: float = 0.32           # music level before ducking under speech

    @classmethod
    def from_dict(cls, raw: dict | None) -> "ShortOptions":
        from recaply.render.presets import EXPORTS
        raw = raw or {}
        o = cls()
        if raw.get("export") in EXPORTS:
            o.export = raw["export"]
        if raw.get("layout") in reframe.STRATEGIES:
            o.layout = raw["layout"]
        if raw.get("captions") in (*subtitles.PRESETS, "none", "brand"):
            o.captions = raw["captions"]
        for k in ("hook", "emoji", "tighten", "use_brand", "broll", "teaser"):
            if k in raw:
                setattr(o, k, bool(raw[k]))
        from recaply.render import bgm as bgm_mod
        choice = str(raw.get("bgm") or "").strip()
        if choice.lower() in ("auto", "none", "") or choice in {t["name"] for t in bgm_mod.tracks()}:
            o.bgm = "" if choice.lower() == "none" else choice
        try:
            o.bgm_level = min(0.8, max(0.05, float(raw.get("bgm_level", o.bgm_level))))
        except (TypeError, ValueError):
            pass
        from recaply.edit.translate import LANGUAGES
        if raw.get("translate") in LANGUAGES:
            o.translate = raw["translate"]
        return o


def teaser_seconds() -> float:
    from recaply.render.teaser import TEASER_SEC
    return TEASER_SEC


def _add_teaser(source: str, body: str, doc: dict, video_only: list, meta: dict, assets: str, edit_dir: str,
                clip: dict, accent: str = "#0865ff", font: str | None = None) -> str:
    """Put the 3 s cinematic teaser in front of `body`. Returns the new file (body is replaced by it)."""
    import subprocess

    from recaply.render import teaser
    t = teaser.pick_time(source, meta["duration"])
    # the still is the reframed picture (same crop as the short) at that moment, without captions or hook text
    still_doc = dict(doc, trim=[t, min(meta["duration"], t + 0.25)], speed=1, fade_in=0, fade_out=0,
                     layers=[L for L in doc["layers"] if L["type"] == "video"] or video_only)
    still_mp4 = os.path.join(edit_dir, "teaser_still.mp4")
    editor.render(source, still_mp4, still_doc, meta, assets)
    still = os.path.join(edit_dir, "teaser_still.png")
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", still_mp4, "-frames:v", "1", still],
                   check=True, capture_output=True)
    ow, oh = editor.output_size(doc)
    text = clip.get("hook") or clip.get("title") or ""
    overlay = teaser.overlay_png(os.path.join(edit_dir, "teaser_overlay.png"), ow, oh, text, accent, font, assets)
    info = editor.probe(body)
    out = body + ".teaser.mp4"
    teaser.build(still, overlay, source, t, body, out, ow, oh, info["fps"], has_audio=bool(meta["audio"]),
                 body_has_audio=bool(info["audio"]))
    os.replace(out, body)
    return body


def render_short(clip: dict, src: str, words: list[dict], out_dir: str, opts: ShortOptions,
                 profile_reframe: str = "center", keywords: tuple[str, ...] = (), owner=None,
                 watermark: bool = False, progress: Callable[[float, str], None] | None = None,
                 telemetry=None, tag: str = "") -> dict:
    """
    Render one short from `src` (the cut clip) with clip-relative `words`.
    `tag` prefixes the file and work-folder names (previews use "preview_", so they never touch the real short).
    Returns {"file", "edit_dir", "layout", "reason", "removed_sec", "duration", "encoder", "sec", "notes"}.
    """
    t_start = time.time()
    report = progress or (lambda f, s: None)
    preset = export_preset(opts.export)
    fmt = preset["format"]
    W, H = editor.canvas_size(fmt)
    cid = clip["id"]
    name = f"{tag}short_{cid:02d}_{fmt.replace(':', 'x')}.mp4"
    edit_dir = os.path.join(out_dir, "_edit", f"{tag}{cid}-{fmt.replace(':', 'x')}")
    shutil.rmtree(edit_dir, ignore_errors=True)
    assets = os.path.join(edit_dir, "assets")
    os.makedirs(assets, exist_ok=True)
    notes: list[str] = []
    cap_layer = None

    kit = brandkit.load(owner) if opts.use_brand else brandkit.BrandKit()
    staged = brandkit.stage_assets(owner, kit, assets) if opts.use_brand else {}

    # 1. tighten
    meta = editor.probe(src)
    source = os.path.join(edit_dir, "source.mp4")
    removed = 0.0
    if opts.tighten and words:
        report(0.02, "Removing pauses and filler words")
        segs, words = tighten.keep_segments(words, meta["duration"])
        removed = tighten.removed_seconds(segs, meta["duration"])
        if removed >= 0.3:
            tighten.apply(src, source, segs, has_audio=bool(meta["audio"]))
        else:
            removed = 0.0
    if not os.path.exists(source):
        shutil.copyfile(src, source)
    meta = editor.probe(source)

    # 2. reframe
    report(0.1, "Finding faces and action")
    preferred = opts.layout if opts.layout != "auto" else profile_reframe
    plan = reframe.choose(source, meta, preferred, ratio(fmt))

    # 2b. B-roll (on the tightened source, after the layout was chosen from the real picture)
    broll_used: list = []
    if opts.broll and words:
        from recaply.edit import broll as broll_mod
        from recaply.providers.llm import get_llm
        report(0.12, "Finding B-roll")
        try:
            br = broll_mod.add(source, source + ".broll.mp4", words, meta["duration"], (meta["w"], meta["h"]),
                               bool(meta["audio"]), os.path.join(edit_dir, "broll"), get_llm(), telemetry)
            if br["file"]:
                os.replace(br["file"], source)
                broll_used = br["inserts"]
            else:
                notes.append(br["note"])
        except Exception as e:  # noqa: BLE001 — the short is still made, without B-roll
            notes.append(f"B-roll skipped: {str(e)[:120]}")

    # 2c. caption translation (timing from the spoken words, text from the translation)
    if opts.translate and words:
        from recaply.edit import translate as tr
        from recaply.providers.llm import get_llm
        report(0.14, "Translating captions")
        try:
            words = tr.translate_words(words, opts.translate, get_llm(), telemetry)
        except Exception as e:  # noqa: BLE001
            notes.append(f"captions not translated: {str(e)[:120]}")

    # 3. document
    layers = reframe.video_layers(plan, meta, W, H)
    video_only = list(layers)                       # the teaser's still is the reframed picture without text
    style = kit.caption_style if opts.captions == "brand" else opts.captions
    if style != "none":
        cap = subtitles.captions_layer(words, style, fmt, plan.strategy, W, H, keywords=keywords,
                                       emphasis=tuple(clip.get("emphasis") or ()), emoji=opts.emoji,
                                       colors=kit.colors() if opts.use_brand else None, font=staged.get("font"))
        if cap:
            layers.append(cap)
            cap_layer = cap
        elif not words:
            notes.append("no speech found, so no captions")
    if opts.hook and clip.get("hook"):
        hk = subtitles.hook_layer(clip["hook"], fmt, W, H, accent=kit.accent, font=staged.get("font"))
        if hk:
            layers.append(hk)
    if staged.get("logo"):
        layers.append(brandkit.logo_layer(kit, staged["logo"], assets, W, H))
    doc = {"format": fmt, "size": preset["size"], "trim": [0, meta["duration"]], "speed": 1, "volume": 1,
           "fade_in": 0, "fade_out": 0.25, "color": {"brightness": 1, "contrast": 1, "saturation": 1},
           "bg": {"type": "color", "color": "#000000", "blur": 50, "opacity": 0.82}, "layers": layers}
    doc = editor.clean_doc(doc, meta, assets)
    with open(os.path.join(edit_dir, "doc.json"), "w", encoding="utf-8") as f:
        json.dump({"doc": doc, "meta": meta, "clip": {k: clip.get(k) for k in ("id", "title", "hook")},
                   "options": asdict(opts), "plan": {"strategy": plan.strategy, "reason": plan.reason}}, f)

    # 4. render
    dst = os.path.join(out_dir, name)
    body = dst + ".body.mp4"
    enc = editor.render(source, body, doc, meta, assets,
                        progress=lambda f, s=None: report(0.15 + 0.8 * f, s or "Rendering"),
                        watermark=watermark)
    # 5. teaser in front, music under everything
    teaser_used, bgm_used = False, None
    if opts.teaser:
        report(0.95, "Adding the cinematic teaser")
        try:
            body = _add_teaser(source, body, doc, video_only, meta, assets, edit_dir, clip, accent=kit.accent,
                               font=staged.get("font"))
            teaser_used = True
        except Exception as e:  # noqa: BLE001 — the short is still made, without the teaser
            notes.append(f"teaser skipped: {str(e)[:140]}")
    if opts.bgm:
        from recaply.render import bgm as bgm_mod
        track = bgm_mod.pick(opts.bgm, cid)
        if track is None:
            notes.append("no music added: put music files in assets/bgm")
        else:
            report(0.97, "Mixing the music")
            try:
                bgm_mod.mix(body, body + ".music.mp4", track, opts.bgm_level, has_audio=bool(editor.probe(body)["audio"]))
                os.replace(body + ".music.mp4", body)
                bgm_used = os.path.basename(track)
            except Exception as e:  # noqa: BLE001
                notes.append(f"music skipped: {str(e)[:140]}")
    if opts.use_brand and (kit.intro or kit.outro):
        report(0.98, "Adding intro and outro")
        if brandkit.add_intro_outro(owner, kit, body, dst, *editor.output_size(doc), fps=meta["fps"]):
            os.remove(body)
    if os.path.exists(body):
        os.replace(body, dst)
    ass_name = None
    if cap_layer:                                   # the same karaoke captions as a subtitle file for other editors
        try:
            from recaply.render import ass
            ass_name = name[:-4] + ".ass"
            ass.write(cap_layer, os.path.join(out_dir, ass_name), W, H, offset=teaser_seconds() if teaser_used else 0.0)
        except Exception as e:  # noqa: BLE001
            ass_name = None
            notes.append(f".ass file skipped: {str(e)[:100]}")
    from recaply.ingest.audio import media_duration
    dur = media_duration(dst)
    note = length_note(preset, dur)
    if note:
        notes.append(note)
    report(1.0, "Done")
    return {"file": name, "edit_dir": os.path.relpath(edit_dir, out_dir).replace("\\", "/"),
            "layout": plan.strategy, "reason": plan.reason, "removed_sec": removed, "duration": round(dur, 2),
            "encoder": enc, "sec": round(time.time() - t_start, 1), "notes": notes, "format": fmt,
            "export": preset["key"], "broll": broll_used, "ass": ass_name, "teaser": teaser_used, "music": bgm_used}
