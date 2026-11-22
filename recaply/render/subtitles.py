"""
Animated captions and the hook overlay for shorts, as editor layers.

Captions are word-timed chunks (a few words on screen, the spoken word
highlighted: karaoke style). On top of the style presets:

- keyword highlighting: profile keywords, numbers and the LLM's emphasis
  words are drawn in the keyword colour,
- emoji: a matching emoji pops above a chunk now and then (optional),
- safe zones: captions sit above the bottom ~25% that TikTok / Shorts /
  Reels cover with their buttons and description, and away from the edges.
"""

from __future__ import annotations

import re

import editor
from captions import chunk_words

# Style presets (the editor's six, same values as static/editor.js, plus three more)
PRESETS: dict[str, dict] = {
    "bold": {"label": "Bold", "per": 3, "size": 84, "upper": True, "color": "#ffffff", "hi": "#ffd21f",
             "hi_mode": "color", "hi_bg": "#0865ff", "stroke": "#000000", "stroke_w": 7, "bg": "#000000",
             "bg_op": 0, "pop": True},
    "box": {"label": "Box", "per": 2, "size": 80, "upper": True, "color": "#ffffff", "hi": "#ffffff",
            "hi_mode": "box", "hi_bg": "#0865ff", "stroke": "#000000", "stroke_w": 0, "bg": "#000000",
            "bg_op": 0, "pop": True},
    "single": {"label": "One word", "per": 1, "size": 110, "upper": True, "color": "#ffffff", "hi": "#ffffff",
               "hi_mode": "none", "hi_bg": "#0865ff", "stroke": "#000000", "stroke_w": 9, "bg": "#000000",
               "bg_op": 0, "pop": True},
    "neon": {"label": "Neon", "per": 3, "size": 80, "upper": True, "color": "#ffffff", "hi": "#3dff8a",
             "hi_mode": "color", "hi_bg": "#0865ff", "stroke": "#0a0a0a", "stroke_w": 8, "bg": "#000000",
             "bg_op": 0, "pop": True, "motion": "stagger"},
    # the three named styles of the studio: a bouncing yellow word, plain white text, words popping in neon
    "bold_yellow": {"label": "Bold & Yellow", "per": 3, "size": 88, "upper": True, "color": "#ffffff", "hi": "#ffe500",
                    "hi_mode": "color", "hi_bg": "#ffe500", "stroke": "#000000", "stroke_w": 9, "bg": "#000000",
                    "bg_op": 0, "pop": True, "motion": "bounce"},
    "minimal_white": {"label": "Minimal White", "per": 5, "size": 56, "upper": False, "color": "#ffffff", "hi": "#ffffff",
                      "hi_mode": "none", "hi_bg": "#ffffff", "stroke": "#000000", "stroke_w": 3, "bg": "#000000",
                      "bg_op": 0, "pop": False, "motion": "none"},
    "clean": {"label": "Clean", "per": 5, "size": 62, "upper": False, "color": "#ffffff", "hi": "#ffd21f",
              "hi_mode": "color", "hi_bg": "#0865ff", "stroke": "#000000", "stroke_w": 4, "bg": "#000000",
              "bg_op": 0, "pop": False},
    "subtitle": {"label": "Subtitle", "per": 7, "size": 52, "upper": False, "color": "#ffffff", "hi": "#ffffff",
                 "hi_mode": "none", "hi_bg": "#0865ff", "stroke": "#000000", "stroke_w": 0, "bg": "#000000",
                 "bg_op": 0.62, "pop": False},
    "karaoke": {"label": "Karaoke", "per": 4, "size": 76, "upper": False, "color": "#ffffff", "hi": "#2ee6ff",
                "hi_mode": "color", "hi_bg": "#0865ff", "stroke": "#000000", "stroke_w": 6, "bg": "#000000",
                "bg_op": 0, "pop": True},
    "punch": {"label": "Punch", "per": 2, "size": 96, "upper": True, "color": "#ffffff", "hi": "#ffffff",
              "hi_mode": "box", "hi_bg": "#ff3d6e", "stroke": "#000000", "stroke_w": 6, "bg": "#000000",
              "bg_op": 0, "pop": True},
    "marker": {"label": "Marker", "per": 3, "size": 78, "upper": False, "color": "#111111", "hi": "#111111",
               "hi_mode": "box", "hi_bg": "#ffe14d", "stroke": "#ffffff", "stroke_w": 0, "bg": "#ffffff",
               "bg_op": 0.92, "pop": True},
}
KEYWORD_COLOR = "#ff9f1c"

EMOJI = [
    (r"\b(money|cash|paid|rich|million|billion|\$\d)", "\U0001F4B0"),       # money bag
    (r"\b(fire|insane|crazy|lit|hot|banger)\b", "\U0001F525"),              # fire
    (r"\b(haha+|lol|lmao|funny|laugh\w*|joke)\b", "\U0001F602"),            # tears of joy
    (r"\b(dead|died|kill\w*|rip)\b", "\U0001F480"),                         # skull
    (r"\b(win|won|winner|champion|victory|clutch|ace)\b", "\U0001F3C6"),    # trophy
    (r"\b(love|heart)\b", "❤️"),                                  # red heart
    (r"\b(wow|what|no way|omg|shock\w*|unbelievable)\b", "\U0001F62E"),     # open mouth
    (r"\b(idea|secret|tip|hack|learn\w*|think)\b", "\U0001F4A1"),           # light bulb
    (r"\b(fast|speed|quick|rocket|grow\w*)\b", "\U0001F680"),               # rocket
    (r"\b(goal|target|focus)\b", "\U0001F3AF"),                             # target
    (r"\b(scared|scary|horror|terrif\w*)\b", "\U0001F631"),                 # scream
    (r"\b(cop|cops|police|sirens?)\b", "\U0001F6A8"),                       # siren
    (r"\b(car|drive|driving|crash\w*)\b", "\U0001F697"),                    # car
    (r"\b(game|gaming|play\w*)\b", "\U0001F3AE"),                           # game controller
]
EMOJI_GAP = 4.0          # seconds between emoji at least
NUMBER = re.compile(r"^\$?\d[\d,.]*[kmb%x]?$|^\d+v\d+$", re.IGNORECASE)


def mark_keywords(words: list[dict], keywords: tuple[str, ...] = (), emphasis: tuple[str, ...] = ()) -> list[dict]:
    """Copies of the words, with k=1 on numbers, single-word profile keywords and emphasis words."""
    singles = {k.lower() for k in keywords if " " not in k and len(k) > 2}
    emph = {e.lower().strip(".,!?\"'") for e in emphasis if e}
    out = []
    for w in words:
        bare = w["w"].lower().strip(".,!?\"'():;")
        x = dict(w)
        if bare and (bare in singles or bare in emph or NUMBER.match(bare)):
            x["k"] = 1
        out.append(x)
    return out


def emoji_for(text: str) -> str | None:
    low = text.lower()
    for pattern, emo in EMOJI:
        if re.search(pattern, low):
            return emo
    return None


def build_chunks(words: list[dict], per: int, emoji: bool = False) -> list[dict]:
    """Caption chunks of up to `per` words (broken at sentence ends and pauses), emoji added sparingly."""
    chunks = chunk_words(words, max_words=per, max_chars=max(10, per * 9), max_gap=0.6)
    last = -99.0
    for c in chunks:
        c["words"] = [{k: v for k, v in w.items() if k in ("w", "s", "e", "p", "k")} for w in c["words"]]
        if emoji:
            t = c["words"][0]["s"]
            emo = emoji_for(" ".join(w["w"] for w in c["words"]))
            if emo and t - last >= EMOJI_GAP:
                c["emoji"] = emo
                last = t
    return chunks


def safe_band(fmt: str, layout: str, W: int, H: int) -> tuple[float, float]:
    """(centre y, height) for the caption block on a W x H canvas."""
    if fmt in ("9:16", "4:5"):
        if layout == "split":
            return H * 0.42, H * 0.16          # on the seam between cam and gameplay
        return H * 0.68, H * 0.16              # above the platform UI at the bottom
    return H * 0.82, H * 0.2


def captions_layer(words: list[dict], preset: str, fmt: str, layout: str, W: int, H: int,
                   keywords: tuple[str, ...] = (), emphasis: tuple[str, ...] = (), emoji: bool = False,
                   colors: dict | None = None, font: str | None = None) -> dict | None:
    """An editor captions layer for `words` (clip-relative seconds), or None when there's no speech."""
    if not words:
        return None
    st = dict(PRESETS.get(preset) or PRESETS["bold"])
    scale = W / 1080
    size = st["size"] * scale * (0.85 if fmt in ("16:9",) else 1.0)
    cy, bh = safe_band(fmt, layout, W, H)
    marked = mark_keywords(words, keywords, emphasis)
    L = {
        "id": "captions", "type": "captions", "name": "Captions", "hidden": False, "locked": False, "opacity": 1,
        "x": round(W * 0.08, 2), "y": round(cy - bh / 2, 2), "w": round(W * 0.84, 2), "h": round(bh, 2),
        "start": 0, "end": None, "kf": {}, "anim_in": {"type": "none", "dur": 0.5},
        "anim_out": {"type": "none", "dur": 0.5},
        "size": int(round(size)), "color": st["color"], "hi": st["hi"], "hi_bg": st["hi_bg"],
        "hi_mode": st["hi_mode"], "stroke": st["stroke"], "stroke_w": round(st["stroke_w"] * scale, 2),
        "bg": st["bg"], "bg_op": st["bg_op"], "upper": st["upper"], "pop": st["pop"], "per": st["per"],
        "preset": preset if preset in PRESETS else "bold", "kw": KEYWORD_COLOR,
        "motion": st.get("motion", "none"),
        "chunks": build_chunks(marked, st["per"], emoji),
    }
    if colors:
        if colors.get("text"):
            L["color"] = colors["text"]
        if colors.get("highlight"):
            if L["hi_mode"] == "box":
                L["hi_bg"] = colors["highlight"]
            else:
                L["hi"] = colors["highlight"]
        if colors.get("keyword"):
            L["kw"] = colors["keyword"]
    if font:
        L["font"] = font
    return L


def hook_layer(text: str, fmt: str, W: int, H: int, accent: str = "#0865ff", duration: float = 3.0,
               font: str | None = None) -> dict | None:
    """On-screen hook for the first seconds: bold text on a coloured box near the top (inside the safe zone)."""
    text = " ".join((text or "").split())[:90]
    if not text:
        return None
    size = int(round((66 if fmt in ("9:16", "4:5") else 54) * W / 1080))
    w = W * 0.86
    measure = editor._font(size).getlength
    lines = editor.wrap_text(text, w - 2 * size * editor.TEXT_PAD, measure)[:3]
    h = len(lines) * size * editor.LINE_H + 2 * size * editor.TEXT_PAD
    top = H * (0.1 if fmt in ("9:16", "4:5") else 0.06)
    L = {
        "id": "hook", "type": "text", "name": "Hook", "hidden": False, "locked": False, "opacity": 1,
        "x": round((W - w) / 2, 2), "y": round(top, 2), "w": round(w, 2), "h": round(h, 2), "rot": 0,
        "start": 0, "end": round(duration, 2), "kf": {},
        "anim_in": {"type": "pop", "dur": 0.35}, "anim_out": {"type": "fade", "dur": 0.3},
        "text": text, "size": size, "color": "#ffffff", "stroke": "#000000", "stroke_w": 0,
        "bg": accent, "bg_op": 0.95, "align": "center", "lines": lines,
    }
    if font:
        L["font"] = font
    return L
