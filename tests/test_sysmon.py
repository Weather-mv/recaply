"""CPU / RAM / GPU readings for the dashboard's system widget."""

from recaply import sysmon
from test_studio_app import client  # noqa: F401  (the fixture)


def _pct(v):
    return v is None or 0 <= v <= 100


def test_snapshot_has_sane_percentages():
    s = sysmon.snapshot()
    assert set(s) == {"cpu", "ram", "gpu", "ts"}
    assert _pct(s["cpu"])
    if s["ram"]:
        assert 0 < s["ram"]["percent"] <= 100 and s["ram"]["total_gb"] >= s["ram"]["used_gb"] > 0
    if s["gpu"]:
        assert _pct(s["gpu"]["percent"]) and _pct(s["gpu"]["mem_percent"]) and s["gpu"]["name"]


def test_a_pc_without_an_nvidia_gpu_reports_none(monkeypatch):
    monkeypatch.setattr(sysmon, "_gpu_cache", (0.0, None))
    monkeypatch.setattr(sysmon.shutil, "which", lambda name: None)
    assert sysmon.gpu() is None


def test_gpu_reading_is_parsed_and_cached(monkeypatch):
    calls = []

    class R:
        returncode = 0
        stdout = "NVIDIA GeForce GTX 1650 Ti, 20, 700, 4096\n"

    monkeypatch.setattr(sysmon, "_gpu_cache", (0.0, None))
    monkeypatch.setattr(sysmon.shutil, "which", lambda name: "nvidia-smi")
    monkeypatch.setattr(sysmon.subprocess, "run", lambda *a, **k: calls.append(a) or R())
    g = sysmon.gpu()
    assert g == {"name": "NVIDIA GeForce GTX 1650 Ti", "percent": 20.0, "mem_percent": 17.1, "used_gb": 0.7, "total_gb": 4.0}
    assert sysmon.gpu() == g and len(calls) == 1                       # nvidia-smi is slow: asked once per few seconds


def test_a_broken_nvidia_smi_does_not_break_the_widget(monkeypatch):
    monkeypatch.setattr(sysmon, "_gpu_cache", (0.0, None))
    monkeypatch.setattr(sysmon.shutil, "which", lambda name: "nvidia-smi")

    def boom(*a, **k):
        raise OSError("driver gone")

    monkeypatch.setattr(sysmon.subprocess, "run", boom)
    assert sysmon.gpu() is None


def test_system_route(client):  # noqa: F811
    d = client.get("/api/studio/system").get_json()
    assert set(d) == {"cpu", "ram", "gpu", "ts"}
