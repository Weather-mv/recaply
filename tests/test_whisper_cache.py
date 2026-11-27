"""The speech model is loaded once and kept warm; a failing GPU load falls back to the CPU loudly."""
import sys
import types

import captions


class FakeModel:
    loads = []

    def __init__(self, name, device, compute_type):
        if device == "cuda" and FakeModel.fail_gpu:
            raise RuntimeError("cublas64_12.dll not found")
        FakeModel.loads.append((name, device, compute_type))

    fail_gpu = False


def _fake(monkeypatch):
    FakeModel.loads, FakeModel.fail_gpu = [], False
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(captions, "IDLE_UNLOAD", 60.0)
    captions.unload()


def test_the_model_is_reused_between_transcriptions(monkeypatch):
    _fake(monkeypatch)
    for _ in range(3):
        m, dev, ct = captions.get_model("large-v3", "cuda", "int8_float16")
        captions.release()
    assert FakeModel.loads == [("large-v3", "cuda", "int8_float16")] and dev == "cuda" and ct == "int8_float16"
    captions.get_model("small", "cpu", "int8")                        # another model replaces it
    captions.release()
    assert FakeModel.loads[-1] == ("small", "cpu", "int8")
    captions.unload()
    assert captions._loaded["model"] is None


def test_a_gpu_that_fails_to_load_falls_back_to_the_cpu_with_a_warning(monkeypatch):
    _fake(monkeypatch)
    FakeModel.fail_gpu = True
    warned = []
    monkeypatch.setattr(captions, "_warned", False)
    monkeypatch.setattr(captions.log, "warning", lambda msg, **kw: warned.append(msg))
    m, dev, ct = captions.get_model("large-v3", "cuda", "int8_float16")
    captions.release()
    assert (dev, ct) == ("cpu", "int8") and FakeModel.loads == [("large-v3", "cpu", "int8")]
    assert warned and "RUNNING ON THE CPU" in warned[0]
    captions.unload()


def test_the_model_is_freed_after_the_idle_time(monkeypatch):
    _fake(monkeypatch)
    monkeypatch.setattr(captions, "IDLE_UNLOAD", 0.0)                # 0 = free at once
    captions.get_model("large-v3", "cuda", "int8_float16")
    assert captions._loaded["model"] is not None
    captions.release()
    assert captions._loaded["model"] is None
