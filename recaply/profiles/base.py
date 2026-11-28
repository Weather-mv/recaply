"""
Content profiles: everything that differs between a podcast, a GTA stream
and a VCT broadcast lives here as data, so the pipeline itself stays generic.

A profile says how much each signal counts, which words in the transcript
mean "something happened", what Whisper should spell right, how long clips
should be, how to reframe them, and (optionally) which game-specific
detector plugins or segmenter to run.

Add a profile: create a module in this package that builds a `Profile` and
calls `register(...)`, then import it in `recaply/profiles/__init__.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("talk", "gaming", "irl")
REFRAMES = ("face", "center", "action", "split")


@dataclass(frozen=True)
class Profile:
    """How to find and shape clips for one kind of content."""

    key: str                                  # url-safe id, e.g. "gaming"
    label: str                                # shown in the UI
    kind: str                                 # talk | gaming | irl
    brief: str                                # what makes a great clip (goes into the LLM prompt)
    weights: dict[str, float]                 # signal name -> weight in the fusion
    keywords: tuple[str, ...] = ()            # transcript words that hint at a moment
    vocab: str = ""                           # Whisper hotwords (names to spell right)
    clip_min: float = 15.0
    clip_max: float = 60.0
    clip_target: float = 35.0
    window_pre: float = 40.0                  # shortlist window before a signal peak (s)
    window_post: float = 25.0                 # ...and after it
    transcribe_all: bool = False              # talk content: sample short probes + energy peaks, transcribe those first, then only the best windows in full
    reframe: str = "center"                   # default 9:16 strategy (Phase 2)
    detectors: tuple[str, ...] = ()           # optional detector plugins (e.g. "valorant_killfeed")
    segmenter: str | None = None              # replaces generic windows (e.g. "valorant_rounds")
    hidden: bool = False                      # not offered by auto-detection

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"profile {self.key}: kind must be one of {KINDS}")
        if self.reframe not in REFRAMES:
            raise ValueError(f"profile {self.key}: reframe must be one of {REFRAMES}")
        if not (5 <= self.clip_min <= self.clip_target <= self.clip_max <= 180):
            raise ValueError(f"profile {self.key}: need 5 <= clip_min <= clip_target <= clip_max <= 180")

    def with_lengths(self, clip_min: float | None = None, clip_max: float | None = None) -> "Profile":
        """A copy with the user's clip length range (kept within 5-180 s and ordered)."""
        lo = min(180.0, max(5.0, float(clip_min if clip_min is not None else self.clip_min)))
        hi = min(180.0, max(lo, float(clip_max if clip_max is not None else self.clip_max)))
        target = min(hi, max(lo, self.clip_target))
        d = {**self.__dict__, "clip_min": lo, "clip_max": hi, "clip_target": target,
             "window_pre": max(self.window_pre, hi * 0.7), "window_post": max(self.window_post, hi * 0.4)}
        return Profile(**d)


_REGISTRY: dict[str, Profile] = {}


def register(profile: Profile) -> Profile:
    _REGISTRY[profile.key] = profile
    return profile


def get_profile(key: str) -> Profile:
    """The profile called `key` (KeyError if unknown)."""
    return _REGISTRY[key]


def all_profiles() -> list[Profile]:
    return list(_REGISTRY.values())


@dataclass(frozen=True)
class Detection:
    """Result of content-type auto-detection."""
    profile: str
    reason: str
    scores: dict = field(default_factory=dict)
