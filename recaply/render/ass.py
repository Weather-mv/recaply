"""
Karaoke subtitles as an .ass file (Advanced SubStation Alpha), for use in other editors and players.

`write(doc_layer, path, W, H)` takes the captions layer the studio renders (words with times, the
preset's colours and motion) and writes one dialogue line per caption chunk. Inside a line each word
has its own timed override tags, so the spoken word switches to the highlight colour and, for the
"bounce" and "stagger" presets, jumps up / pops in (`\\t` transforms: libass, VLC, mpv, Premiere,
DaVinci, CapCut and ffmpeg's `ass=` filter all read them).

ffmpeg can burn the file into a video:  ffmpeg -i in.mp4 -vf "ass=captions.ass" out.mp4
"""

from __future__ import annotations

import re

BOUNCE_MS = 220          # the word animation, like editor.CAP_WORD
PLAIN = re.compile(r"[\r\n]+")


def _bgr(hexcol: str, alpha: float = 0.0) -> str:
    """#rrggbb -> ASS &HAABBGGRR (alpha 0 = opaque)."""
    h = (hexcol or "#ffffff").lstrip("#")
    if len(h) != 6:
        h = "ffffff"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{int(round(alpha * 255)):02X}{b}{g}{r}".upper()


def _stamp(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _text(word: str, upper: bool) -> str:
    w = PLAIN.sub(" ", word or "")
    w = w.upper() if upper else w
    return w.replace("\\", "").replace("{", "(").replace("}", ")")


def _chunk_end(chunks: list[dict], i: int, gap: float = 0.6, tail: float = 0.3) -> float:
    last = chunks[i]["words"][-1]["e"]
    nxt = chunks[i + 1]["words"][0]["s"] if i + 1 < len(chunks) else float("inf")
    return nxt if nxt - last < gap else min(nxt, last + tail)


def build(layer: dict, W: int, H: int, font: str = "Arial", offset: float = 0.0) -> str:
    """The .ass text for a captions layer (see recaply.render.subtitles.captions_layer).
    `offset` shifts every line later by that many seconds (a teaser placed in front of the short)."""
    size = int(layer.get("size") or 70)
    motion = layer.get("motion", "none")
    hi_color = layer.get("hi_mode") == "color"
    base, hi, kw = layer.get("color", "#ffffff"), layer.get("hi", "#ffd21f"), layer.get("kw", layer.get("hi", "#ff9f1c"))
    cx = int(round(layer["x"] + layer["w"] / 2))
    cy = int(round(layer["y"] + layer["h"] / 2))
    box = layer.get("bg_op", 0) > 0
    out_w = float(layer.get("stroke_w") or 0)
    head = [
        "[Script Info]", "; Made by Clipforge", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Default,{font},{size},{_bgr(base)},{_bgr(hi)},{_bgr(layer.get('stroke', '#000000'))},"
        f"{_bgr(layer.get('bg', '#000000'), 1 - float(layer.get('bg_op', 0)))},-1,0,0,0,100,100,0,0,"
        f"{3 if box else 1},{out_w:.1f},0,5,{int(W * 0.08)},{int(W * 0.08)},0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    chunks = layer.get("chunks") or []
    lines = []
    for i, c in enumerate(chunks):
        words = c["words"]
        if not words:
            continue
        t0, t1 = words[0]["s"], _chunk_end(chunks, i)
        shift = float(offset)
        parts = []
        for k, w in enumerate(words):
            a = int(round((w["s"] - t0) * 1000))                                  # this word is spoken from a ...
            b = int(round(((words[k + 1]["s"] if k + 1 < len(words) else t1) - t0) * 1000))   # ... until b
            col = kw if w.get("k") else base
            tags = [f"\\1c{_bgr(col)}"]
            if motion == "stagger":
                tags.append("\\alpha&HFF&")                                     # hidden until spoken, then pops in
                tags.append(f"\\t({a},{a},\\alpha&H00&)\\fscx55\\fscy55\\t({a},{a + BOUNCE_MS},\\fscx100\\fscy100)")
            if hi_color:
                tags.append(f"\\t({a},{a},\\1c{_bgr(hi)})\\t({b},{b},\\1c{_bgr(col)})")
            if motion == "bounce":
                tags.append(f"\\t({a},{a + BOUNCE_MS // 2},\\fscx122\\fscy122)\\t({a + BOUNCE_MS // 2},{a + BOUNCE_MS},\\fscx100\\fscy100)")
            parts.append("{" + "".join(tags) + "}" + _text(w["w"], bool(layer.get("upper"))))
        lines.append(f"Dialogue: 0,{_stamp(t0 + shift)},{_stamp(t1 + shift)},Default,,0,0,0,,{{\\an5\\pos({cx},{cy})}}" + " ".join(parts))
    return "\n".join(head + lines) + "\n"


def write(layer: dict, path: str, W: int, H: int, font: str = "Arial", offset: float = 0.0) -> str:
    """Write the layer's .ass file to `path` (UTF-8 with BOM, which every player reads). Returns `path`."""
    with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(build(layer, W, H, font, offset))
    return path
