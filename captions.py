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
import sys
import threading
import time

import numpy as np

MODELS = {"best": "large-v3", "fast": "large-v3-turbo"}
DEFAULT_MODEL = os.environ.get("RECAPLY_WHISPER_MODEL", "")   # overrides the choice when set
DEVICE = os.environ.get("RECAPLY_WHISPER_DEVICE", "auto")     # auto / cuda / cpu
# Names Whisper should spell right. The default is the Valorant profile's list (the
# editor's captions were built for Valorant clips); the clip pipeline passes each
# profile's own vocabulary instead.
from recaply.profiles.valorant.vocab import VOCAB  # noqa: E402
from recaply.log import get_logger  # noqa: E402
LOCK = threading.Lock()                 # one transcription at a time (GPU memory)
_dll_dirs_added = False


COMPUTE = os.environ.get("RECAPLY_WHISPER_COMPUTE", "")      # e.g. int8_float16 / float16 / int8; default picks the best
IDLE_UNLOAD = float(os.environ.get("RECAPLY_WHISPER_IDLE", "300"))  # seconds a loaded model stays in (GPU) memory

log = get_logger("recaply.whisper")


def _nvidia_dirs():
    """Folders holding the cuBLAS / cuDNN DLLs: pip's nvidia-* packages, or the copy bundled in the app."""
    bases = []
    try:
        import nvidia
        bases += list(getattr(nvidia, "__path__", []))
    except ImportError:
        pass
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen and os.path.isdir(os.path.join(frozen, "nvidia")):
        bases.append(os.path.join(frozen, "nvidia"))
    out = []
    for base in bases:
        out += glob.glob(os.path.join(base, "*", "bin")) + glob.glob(os.path.join(base, "*", "lib"))
    return out


def _cuda_dlls():
    """Make the cuBLAS / cuDNN DLLs findable (Windows)."""
    global _dll_dirs_added
    if _dll_dirs_added:
        return
    _dll_dirs_added = True
    if True:
        for d in _nvidia_dirs():
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


_warned = False


def cpu_warning(reason):
    """A loud log line when an NVIDIA card is there but speech-to-text runs on the CPU (several times slower)."""
    global _warned
    if _warned:
        return
    _warned = True
    bar = "!" * 78
    log.warning(f"\n{bar}\n  SPEECH-TO-TEXT IS RUNNING ON THE CPU, NOT THE GPU ({reason}).\n"
                f"  It works, but transcription is several times slower. To use the NVIDIA card:\n"
                f"    pip install -r requirements-gpu.txt   (cuBLAS + cuDNN for CUDA 12)\n{bar}")


def _device():
    _cuda_dlls()
    import ctranslate2
    gpus = ctranslate2.get_cuda_device_count()
    if DEVICE in ("cuda", "cpu"):
        dev = DEVICE
    elif gpus > 0 and _cublas_loads():
        dev = "cuda"
    else:
        dev = "cpu"
        if gpus > 0:
            cpu_warning("cuBLAS / cuDNN not found")
    if dev == "cuda":
        types = ctranslate2.get_supported_compute_types("cuda")
        if COMPUTE and COMPUTE in types:
            return dev, COMPUTE
        return dev, "int8_float16" if "int8_float16" in types else "float16" if "float16" in types else "float32"
    return dev, COMPUTE if COMPUTE in ("int8", "float32", "int8_float32") else "int8"


# ---------- one loaded model, shared and kept warm ----------
# Loading large-v3 takes several seconds; it used to happen on every transcription. The model now stays
# loaded for IDLE_UNLOAD seconds after its last use, then is freed so the GPU memory goes back to
# rendering. Only one model is kept (a 4 GB card can't hold two).

_model_lock = threading.Lock()
_loaded = {"key": None, "model": None, "users": 0}
_unload_timer = None


def get_model(name, dev, ctype):
    """(model, dev, ctype): the cached model when it matches, else a fresh one (CUDA failing -> CPU,
    with a loud warning). Call `release()` when done with it."""
    from faster_whisper import WhisperModel
    global _unload_timer
    with _model_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()
            _unload_timer = None
        key = (name, dev, ctype)
        if _loaded["key"] != key or _loaded["model"] is None:
            _drop_locked()
            t = time.perf_counter()
            try:
                model = WhisperModel(name, device=dev, compute_type=ctype)
            except Exception as e:  # noqa: BLE001 — e.g. no CUDA runtime: fall back to the CPU
                if dev == "cpu":
                    raise
                cpu_warning(f"the GPU model failed to load: {str(e)[:120]}")
                dev, ctype = "cpu", "int8"
                key = (name, dev, ctype)
                model = WhisperModel(name, device=dev, compute_type=ctype)
            _loaded.update(key=key, model=model)
            log.info("whisper model loaded", model=name, device=dev, compute=ctype,
                     seconds=round(time.perf_counter() - t, 1))
        _loaded["users"] += 1
        return _loaded["model"], _loaded["key"][1], _loaded["key"][2]


def release():
    """Done with the model: free it after IDLE_UNLOAD idle seconds (at once when IDLE_UNLOAD is 0)."""
    global _unload_timer
    with _model_lock:
        _loaded["users"] = max(0, _loaded["users"] - 1)
        if _loaded["users"]:
            return
        if IDLE_UNLOAD <= 0:
            _drop_locked()
            return
        _unload_timer = threading.Timer(IDLE_UNLOAD, unload)
        _unload_timer.daemon = True
        _unload_timer.start()


def unload():
    with _model_lock:
        if not _loaded["users"]:
            _drop_locked()


def _drop_locked():
    if _loaded["model"] is not None:
        _loaded.update(model=None, key=None)
        gc.collect()


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
    name = DEFAULT_MODEL or MODELS.get(quality, MODELS["best"])
    with LOCK:
        dev, ctype = _device()
        if progress and _loaded["key"] != (name, dev, ctype):
            progress("Downloading the speech model (first time only)" if not model_cached(name)
                     else "Loading the speech model", None)
        model, dev, ctype = get_model(name, dev, ctype)
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
            from recaply.providers.transcribe.guard import drop_hallucinations, suspect_segment
            words = []
            for seg in segments:
                if progress:
                    progress("Transcribing", min(1.0, seg.end / total))
                if suspect_segment(seg):              # invented on music / noise: no captions for it
                    continue
                for w in seg.words or []:
                    text = w.word.strip()
                    if not text:
                        continue
                    s, e = float(w.start), float(w.end)
                    e = min(e, s + max(0.25, 0.09 * len(text) + 0.35))   # a word before a pause
                    words.append({"w": text, "s": round(start + s, 3), "e": round(start + max(e, s + 0.06), 3),
                                  "p": round(float(w.probability), 3)})
            words = drop_hallucinations(words)
            # keep words in order and non-overlapping
            for a, b in zip(words, words[1:]):
                if b["s"] < a["e"]:
                    a["e"] = max(a["s"] + 0.04, b["s"])
            return words, {"language": info.language, "language_probability": round(info.language_probability, 3),
                           "duration": round(total, 2), "model": name, "device": dev}
        finally:
            del model
            release()


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
