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
        for k in ("hook", "emoji", "tighten", "use_brand"):
            if k in raw:
                setattr(o, k, bool(raw[k]))
        return o


def render_short(clip: dict, src: str, words: list[dict], out_dir: str, opts: ShortOptions,
                 profile_reframe: str = "center", keywords: tuple[str, ...] = (), owner=None,
                 watermark: bool = False, progress: Callable[[float, str], None] | None = None) -> dict:
    """
    Render one short from `src` (the cut clip) with clip-relative `words`.
    Returns {"file", "edit_dir", "layout", "reason", "removed_sec", "duration", "encoder", "sec", "notes"}.
    """
    t_start = time.time()
    report = progress or (lambda f, s: None)
    preset = export_preset(opts.export)
    fmt = preset["format"]
    W, H = editor.canvas_size(fmt)
    cid = clip["id"]
    name = f"short_{cid:02d}_{fmt.replace(':', 'x')}.mp4"
    edit_dir = os.path.join(out_dir, "_edit", f"{cid}-{fmt.replace(':', 'x')}")
    shutil.rmtree(edit_dir, ignore_errors=True)
    assets = os.path.join(edit_dir, "assets")
    os.makedirs(assets, exist_ok=True)
    notes: list[str] = []

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

    # 3. document
    layers = reframe.video_layers(plan, meta, W, H)
    style = kit.caption_style if opts.captions == "brand" else opts.captions
    if style != "none":
        cap = subtitles.captions_layer(words, style, fmt, plan.strategy, W, H, keywords=keywords,
                                       emphasis=tuple(clip.get("emphasis") or ()), emoji=opts.emoji,
                                       colors=kit.colors() if opts.use_brand else None, font=staged.get("font"))
        if cap:
            layers.append(cap)
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
    if opts.use_brand and (kit.intro or kit.outro):
        report(0.96, "Adding intro and outro")
        if brandkit.add_intro_outro(owner, kit, body, dst, *editor.output_size(doc), fps=meta["fps"]):
            os.remove(body)
    if os.path.exists(body):
        os.replace(body, dst)
    from recaply.ingest.audio import media_duration
    dur = media_duration(dst)
    note = length_note(preset, dur)
    if note:
        notes.append(note)
    report(1.0, "Done")
    return {"file": name, "edit_dir": os.path.relpath(edit_dir, out_dir).replace("\\", "/"),
            "layout": plan.strategy, "reason": plan.reason, "removed_sec": removed, "duration": round(dur, 2),
            "encoder": enc, "sec": round(time.time() - t_start, 1), "notes": notes, "format": fmt,
            "export": preset["key"]}
