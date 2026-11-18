"""
captions.py — speech to text with word timings, for the editor's auto captions.

Uses faster-whisper (OpenAI Whisper models on CTranslate2): large-v3 for the
highest accuracy, or large-v3-turbo for speed. On an NVIDIA GPU it runs with
CUDA (the cuBLAS / cuDNN libraries come from pip's nvidia-* packages), on
other machines on the CPU (int8). The model is downloaded from Hugging Face on
first use and cached.

Accuracy helpers:
  - audio is decoded by ffmpeg to 16 kHz mono (what Whisper expects);
  - Silero VAD skips silence, which stops Whisper inventing words there;
  - a vocabulary of Valorant / VCT names (plus the user's own words) is passed
    as hotwords on every window, so "Killjoy", "Sentinels" or "tarik" come out
    spelled right;
  - word timings come from Whisper's cross-attention alignment; overlong words
    (a word followed by a pause) are clipped so captions don't linger.

Output: [{"w": "word", "s": start, "e": end, "p": probability}] in source
seconds, then grouped into caption chunks by `chunk_words`.
"""

import gc
import glob
import os
import subprocess
import threading

import numpy as np

MODELS = {"best": "large-v3", "fast": "large-v3-turbo"}
DEFAULT_MODEL = os.environ.get("RECAPLY_WHISPER_MODEL", "")   # overrides the choice when set
DEVICE = os.environ.get("RECAPLY_WHISPER_DEVICE", "auto")     # auto / cuda / cpu
VOCAB = ("Valorant, VCT, Champions, Masters, Sentinels, G2, Paper Rex, PRX, Fnatic, NRG, Cloud9, LOUD, "
         "Leviathan, Team Heretics, Team Liquid, DRX, Gen.G, T1, EDward Gaming, Karmine Corp, 100 Thieves, "
         "Jett, Raze, Omen, Sova, Viper, Killjoy, Cypher, Reyna, Phoenix, Sage, Brimstone, Breach, Skye, "
         "Yoru, Astra, KAY/O, Chamber, Neon, Fade, Harbor, Gekko, Deadlock, Iso, Clove, Vyse, Tejo, Waylay, "
         "Ascent, Lotus, Haven, Bind, Split, Icebox, Breeze, Sunset, Pearl, Fracture, Abyss, Corrode, "
         "Vandal, Phantom, Operator, Sheriff, Ghost, Spectre, Odin, spike, defuse, clutch, ace, eco, "
         "tarik, TenZ, zekken, Demon1, aspas, Derke, Boaster, Chronicle, something, f0rsakeN, Jinggg")
LOCK = threading.Lock()                 # one transcription at a time (GPU memory)
_dll_dirs_added = False


def _cuda_dlls():
    """Make the pip-installed cuBLAS / cuDNN DLLs findable (Windows)."""
    global _dll_dirs_added
    if _dll_dirs_added:
        return
    _dll_dirs_added = True
    try:
        import nvidia
    except ImportError:
        return
    for base in list(getattr(nvidia, "__path__", [])):
        for d in glob.glob(os.path.join(base, "*", "bin")) + glob.glob(os.path.join(base, "*", "lib")):
            if hasattr(os, "add_dll_directory") and os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _cublas_loads():
    """An NVIDIA card isn't enough: CUDA's cuBLAS must be installed too (the Windows app
    doesn't ship it), and it's only loaded at the first transcription, too late to fall back."""
    if os.name != "nt":
        return True
    import ctypes
    try:
        ctypes.WinDLL("cublas64_12.dll")
        return True
    except OSError:
        return False


def _device():
    _cuda_dlls()
    import ctranslate2
    if DEVICE in ("cuda", "cpu"):
        dev = DEVICE
    else:
        dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 and _cublas_loads() else "cpu"
    if dev == "cuda":
        types = ctranslate2.get_supported_compute_types("cuda")
        return dev, "int8_float16" if "int8_float16" in types else "float16" if "float16" in types else "float32"
    return dev, "int8"


def model_cached(name):
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(f"Systran/faster-whisper-{name}", "model.bin"), str) or (
            isinstance(try_to_load_from_cache(f"mobiuslabsgmbh/faster-whisper-{name}", "model.bin"), str))
    except Exception:  # noqa: BLE001
        return False


def load_audio(path, start=0.0, end=None):
    """16 kHz mono float32 samples of [start, end] seconds."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-t", f"{max(0.1, end - start):.3f}"]
    cmd += ["-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, timeout=1800).stdout
    return np.frombuffer(raw, np.float32).copy()


def transcribe(path, start=0.0, end=None, quality="best", language=None, vocab="", progress=None):
    """
    Words spoken in [start, end] of `path`, in source seconds. `progress(stage,
    fraction)` is called as it goes. Returns (words, info dict).
    """
    from faster_whisper import WhisperModel
    name = DEFAULT_MODEL or MODELS.get(quality, MODELS["best"])
    with LOCK:
        dev, ctype = _device()
        if progress:
            progress("Downloading the speech model (first time only)" if not model_cached(name)
                     else "Loading the speech model", None)
        try:
            model = WhisperModel(name, device=dev, compute_type=ctype)
        except Exception:  # noqa: BLE001 — e.g. no CUDA runtime: fall back to the CPU
            if dev == "cpu":
                raise
            dev, ctype = "cpu", "int8"
            model = WhisperModel(name, device=dev, compute_type=ctype)
        try:
            if progress:
                progress("Reading the audio", None)
            audio = load_audio(path, start, end)
            total = len(audio) / 16000
            if total < 0.3:
                return [], {"language": None, "duration": total, "model": name, "device": dev}
            hot = ", ".join(x for x in (VOCAB, (vocab or "").strip()) if x)
            segments, info = model.transcribe(
                audio, language=language or None, word_timestamps=True, beam_size=5, best_of=5,
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
                condition_on_previous_text=False, hotwords=hot[:900],
                initial_prompt="Hello, welcome back! Okay, let's go. Wait, what? That's insane.",
                no_speech_threshold=0.6, compression_ratio_threshold=2.4)
            words = []
            for seg in segments:
                if progress:
                    progress("Transcribing", min(1.0, seg.end / total))
                for w in seg.words or []:
                    text = w.word.strip()
                    if not text:
                        continue
                    s, e = float(w.start), float(w.end)
                    e = min(e, s + max(0.25, 0.09 * len(text) + 0.35))   # a word before a pause
                    words.append({"w": text, "s": round(start + s, 3), "e": round(start + max(e, s + 0.06), 3),
                                  "p": round(float(w.probability), 3)})
            # keep words in order and non-overlapping
            for a, b in zip(words, words[1:]):
                if b["s"] < a["e"]:
                    a["e"] = max(a["s"] + 0.04, b["s"])
            return words, {"language": info.language, "language_probability": round(info.language_probability, 3),
                           "duration": round(total, 2), "model": name, "device": dev}
        finally:
            del model
            gc.collect()


def chunk_words(words, max_words=3, max_chars=22, max_gap=0.6):
    """
    Group words into caption chunks (what's on screen at once): at most
    `max_words` words / `max_chars` characters, broken at sentence ends and
    pauses longer than `max_gap`.
    """
    chunks, cur = [], []
    for w in words:
        if cur:
            gap = w["s"] - cur[-1]["e"]
            chars = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            ends = cur[-1]["w"][-1:] in ".?!"
            if len(cur) >= max_words or chars > max_chars or gap > max_gap or ends:
                chunks.append({"words": cur})
                cur = []
        cur.append(dict(w))
    if cur:
        chunks.append({"words": cur})
    return chunks
