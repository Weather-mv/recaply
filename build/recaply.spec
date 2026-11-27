# PyInstaller spec for the Clipforge Windows app (run through build/build.ps1).
#
# SECURITY: only the folders listed in `datas` are bundled. The project's .env
# (storage keys) and cookies.txt (a YouTube login) must never be added here;
# build.ps1 checks the finished folder for them and stops if it finds either.
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
STAGE = os.path.join(SPECPATH, "stage")          # made by build.ps1: bin/ (ffmpeg, ffprobe, node), app.env

datas = [
    (os.path.join(ROOT, "templates"), "templates"),
    (os.path.join(ROOT, "static"), "static"),
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(STAGE, "bin"), "bin"),
    (os.path.join(STAGE, "app.env"), "."),       # RECAPLY_CLOUD_URL for this build, read as .env
    (os.path.join(SPECPATH, "recaply.ico"), "."),  # the tray icon
]
binaries = []
hiddenimports = ["app", "desktop_auth", "captions", "reels", "editor", "live", "killer_id",
                 "round_detector", "pipeline", "waitress", "webview.platforms.edgechromium",
                 "pystray._win32", "clip_routes"]
# The clipping core: profiles, signals and providers are looked up by name at runtime.
# (rq is only for the Docker/server queue, never in the Windows app.)
hiddenimports += [m for m in collect_submodules("recaply") if not m.endswith("rq_queue")]

# Packages that load models, data files or native libraries at runtime.
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "rapidocr", "yt_dlp", "yt_dlp_ejs", "webview",
            "tokenizers", "av", "pythonnet", "clr_loader"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("certifi")

# GPU speech-to-text: build.ps1 -WithCuda bundles the CUDA 12 cuBLAS / cuDNN DLLs from the .venv's
# nvidia-* packages (about 2 GB more) into nvidia/<pkg>/bin, where captions.py looks for them.
# Without it, installed copies transcribe on the CPU and log a loud warning.
WITH_CUDA = os.environ.get("RECAPLY_BUNDLE_CUDA") == "1"
if WITH_CUDA:
    import glob
    import nvidia
    found = 0
    for base in nvidia.__path__:
        for pkg in ("cublas", "cudnn", "cuda_nvrtc"):
            for dll in glob.glob(os.path.join(base, pkg, "bin", "*.dll")):
                binaries.append((dll, f"nvidia/{pkg}/bin"))
                found += 1
    if not found:
        raise SystemExit("-WithCuda: no CUDA DLLs found. Run: .venv\Scripts\pip install -r requirements-gpu.txt")
    print(f"Bundling {found} CUDA DLLs for GPU speech-to-text")
# onnxruntime's model-conversion tools aren't used at runtime, and their deep paths can pass
# Windows' 260-character limit when the app is installed in a long folder.
_dev_only = ("onnxruntime/tools", "onnxruntime/quantization", "onnxruntime/transformers")
datas = [d for d in datas if not any(x in d[1].replace("\\", "/") for x in _dev_only)]
hiddenimports = [h for h in hiddenimports if not h.startswith(("onnxruntime.tools", "onnxruntime.quantization",
                                                               "onnxruntime.transformers"))]

a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        # not used by the Windows app
        "boto3", "botocore", "s3transfer", "storage_tests", "moto",
        # the nvidia-* Python packages themselves: with -WithCuda only their DLLs are bundled (above)
        "nvidia",
        # build and dev tools
        "PyInstaller", "pip", "setuptools", "tkinter", "pydevd", "IPython", "pytest", "matplotlib",
        "omegaconf", "antlr4",
    ],
    noarchive=False,
)
# PyInstaller sees that bin/ffmpeg.exe needs the ffmpeg DLLs and copies them to the top
# level too; they're already next to ffmpeg in bin/ (and PyAV has its own), so drop the copies.
_bin_dlls = {n.lower() for n in os.listdir(os.path.join(STAGE, "bin")) if n.lower().endswith(".dll")}
a.binaries = [b for b in a.binaries if not (os.path.dirname(b[0]) in ("", ".") and os.path.basename(b[0]).lower() in _bin_dlls)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Recaply",
    icon=os.path.join(SPECPATH, "recaply.ico"),
    console=False,
    version=os.path.join(STAGE, "version.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Recaply", upx=False)
