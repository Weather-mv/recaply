"""
desktop.py — starts Clipforge as a Windows app.

It runs the app (app.py) on a private local server, 127.0.0.1 on a free
port, and shows it in a native window (Edge WebView2, part of Windows 10/11).
Everything (recaps, clips, reels, the editor) stays on this PC under
%LOCALAPPDATA%\\Recaply; only sign-in and the plan go to the online service.

    python desktop.py            # from source (data in ./clips)
    Recaply.exe                  # installed (see build/)
    Recaply.exe --helper         # no window: runs in the tray for the web app

The same local server answers the website's web app (/app), which opens
Clipforge in a browser tab instead of the window; see desktop_auth.py. Starting
it again while it's open brings the open window to the front.
"""

import json
import multiprocessing
import os
import secrets
import sys
import threading
import time
import urllib.request

SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#08080a;color:#fff;font:600 17px 'Segoe UI',system-ui,sans-serif}
@media (prefers-color-scheme: light){html,body{background:#f8f8f8;color:#0a0a0c}}
.c{height:100%;display:grid;place-items:center;align-content:center;gap:22px}
.logo{width:64px;height:64px;border-radius:18px;background:linear-gradient(135deg,#3984ff,#0101db);display:grid;place-items:center;box-shadow:0 14px 40px rgb(8 101 255/.35)}
.bar{width:180px;height:4px;border-radius:9px;background:rgb(128 128 128/.25);overflow:hidden}
.bar i{display:block;width:40%;height:100%;border-radius:9px;background:#0865ff;animation:m 1.1s ease-in-out infinite}
@keyframes m{0%{transform:translateX(-100%)}100%{transform:translateX(260%)}}
</style></head><body><div class="c"><div class="logo"><svg width="34" height="34" viewBox="0 0 32 32">
<path d="M22.6 10.6A8 8 0 1 0 24 16" fill="none" stroke="white" stroke-width="2.6" stroke-linecap="round"/>
<path d="M24.4 6.8v4.6h-4.6" fill="none" stroke="white" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M14 12.3v7.4l5.8-3.7z" fill="white"/></svg></div><div>Starting Clipforge…</div><div class="bar"><i></i></div></div></body></html>"""


def hide_console_windows():
    """A windowed app has no console, so Windows opens a new console window for every
    ffmpeg / ffprobe / node / yt-dlp it starts. Start them all hidden instead."""
    if os.name != "nt":
        return
    import subprocess
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init


hide_console_windows()     # module level: editor worker processes import this file too


def app_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Recaply")


def prepare_environment():
    """Settings and paths that must be in place before any Clipforge module is imported."""
    os.environ.setdefault("RECAPLY_DESKTOP", "1")
    os.makedirs(app_dir(), exist_ok=True)
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    bundled = os.path.join(base, "bin")                 # ffmpeg, ffprobe and node from the installer
    if os.path.isdir(bundled):
        os.environ["PATH"] = bundled + os.pathsep + os.environ.get("PATH", "")
    # A YouTube cookies.txt is optional and per user: it lives with the user's data, never in the app.
    os.environ.setdefault("RECAPLY_COOKIES", os.path.join(app_dir(), "cookies.txt"))
    if getattr(sys, "frozen", False):
        # A windowed app has no console: keep a log instead.
        log = open(os.path.join(app_dir(), "recaply.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"\n--- Clipforge started {time.strftime('%Y-%m-%d %H:%M:%S')} ---")


INSTANCE = None   # path of the file that tells a second launch where the open window is


def other_instance(quiet):
    """If Clipforge is already running, ask it to show its window (unless `quiet`) and return True."""
    try:
        with open(INSTANCE, encoding="utf-8") as f:
            info = json.load(f)
        url = f"http://127.0.0.1:{info['port']}/__show?k={info['show']}" + ("&quiet=1" if quiet else "")
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except (OSError, ValueError, KeyError):
        return False


def bind_server(app):
    """The local server, on the first free helper port (so the website's web app can find it)."""
    from config import HELPER_PORTS
    from waitress import create_server
    for port in (*HELPER_PORTS, 0):
        try:
            return create_server(app, host="127.0.0.1", port=port, threads=16, channel_timeout=300, ident="Clipforge")
        except OSError:
            continue
    raise OSError("No free port for Clipforge's local server")


def tray_icon(actions):
    """Clipforge's icon in the system tray (it keeps running there for the web app). None without pystray."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        return None
    base = getattr(sys, "_MEIPASS", None) or os.path.join(os.path.dirname(os.path.abspath(__file__)), "build")
    try:
        image = Image.open(os.path.join(base, "recaply.ico"))
    except OSError:
        image = Image.new("RGBA", (64, 64), "#0865ff")
    menu = pystray.Menu(
        pystray.MenuItem("Open Clipforge", lambda: actions["show"](), default=True),
        pystray.MenuItem("Open the web app in my browser", lambda: actions["web"]()),
        pystray.MenuItem("Open videos folder", lambda: actions["folder"]()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Clipforge", lambda: actions["quit"]()))
    icon = pystray.Icon("Clipforge", image, "Clipforge", menu)
    threading.Thread(target=icon.run, daemon=True, name="tray").start()
    return icon


def ask(title, text):
    """A Yes / No box (from the tray, where there may be no window to show it in)."""
    if os.name != "nt":
        return True
    import ctypes
    return ctypes.windll.user32.MessageBoxW(None, text, title, 0x4 | 0x30 | 0x40000) == 6   # Yes/No, warning, on top


def main():
    """`Recaply.exe` opens the window. `Recaply.exe --helper` (the website's "Start Clipforge"
    button through recaply://, or Windows start-up) runs in the tray for the web app only."""
    global INSTANCE
    multiprocessing.freeze_support()        # editor worker processes start here and stop at this line
    args = [a.lower() for a in sys.argv[1:]]
    helper = "--helper" in args or any(a.startswith("recaply:") for a in args)
    quiet = "--quiet" in args               # Windows start-up: no "running in the tray" message
    prepare_environment()
    INSTANCE = os.path.join(app_dir(), "instance.json")
    if other_instance(quiet=helper):
        return

    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window("Clipforge", html=SPLASH, width=1400, height=900, min_size=(1024, 680),
                                   background_color="#08080a", text_select=True, hidden=helper)
    # Started for the web app, the window stays hidden (and loads nothing) until it's opened from the tray.
    state = {"app": None, "url": None, "loaded": False, "icon": None}
    show_key = secrets.token_urlsafe(16)

    def show_window():
        if state["url"] and not state["loaded"]:
            state["loaded"] = True
            window.load_url(state["url"])
        window.show()
        window.restore()

    def busy():
        recaply = state["app"]
        return bool(recaply) and (any(recaply.busy(j) for j in list(recaply.JOBS.values())) or any(
            recaply.editor_routes.project_busy(p) for p in list(recaply.editor_routes.PROJECTS.values())))

    def stop():
        if state["icon"]:
            state["icon"].stop()
        try:
            os.remove(INSTANCE)
        except OSError:
            pass
        os._exit(0)                               # stop background work right away

    def quit_app():
        if busy() and not ask("Clipforge is still working",
                              "Downloads, reels or exports are still running and will stop. Quit anyway?"):
            return
        stop()

    def open_web():
        import webbrowser
        from config import CLOUD_URL
        webbrowser.open(f"{CLOUD_URL}/app")

    def open_folder():
        from config import CLIPS_DIR
        if os.name == "nt":
            os.startfile(CLIPS_DIR)  # noqa: S606 — opens Explorer on our own data folder

    def boot():
        try:
            import desktop_auth
            import app as recaply                       # loads recaps, projects and the models' code
            from flask import abort, request

            @recaply.app.route("/__show")
            def show():
                if request.args.get("k") != show_key:
                    abort(403)
                if not request.args.get("quiet"):
                    show_window()
                return "ok"

            server = bind_server(recaply.app)
            port = server.effective_port
            desktop_auth.PORT["n"] = port
            state.update(app=recaply, url=f"http://127.0.0.1:{port}/__open?k={desktop_auth.LAUNCH_KEY}")
            threading.Thread(target=server.run, daemon=True, name="server").start()
            with open(INSTANCE, "w", encoding="utf-8") as f:
                json.dump({"port": port, "show": show_key, "pid": os.getpid()}, f)
            print(f"[recaply] local server on http://127.0.0.1:{port} ({'tray' if helper else 'window'})")
            state["icon"] = tray_icon({"show": show_window, "web": open_web, "folder": open_folder, "quit": quit_app})
            if not helper:
                state["loaded"] = True
                window.load_url(state["url"])
            elif state["icon"] and not quiet:
                state["icon"].notify("Clipforge is running in the tray. Go back to your browser to use the web app.",
                                     "Clipforge is ready")
        except Exception as e:  # noqa: BLE001 — show it instead of a blank window
            import traceback
            traceback.print_exc()
            window.load_html(f"<body style='font:16px Segoe UI;padding:40px;background:#08080a;color:#fff'>"
                             f"<h2>Clipforge couldn't start</h2><p>{type(e).__name__}: {str(e)[:400]}</p>"
                             f"<p>Details are in {os.path.join(app_dir(), 'recaply.log')}</p></body>")
            window.show()

    def closing():
        """With the tray icon, closing the window keeps Clipforge running there (for the web
        app); "Quit Clipforge" in the tray stops it. Without it, ask before closing while
        downloads, reels or exports are still running."""
        if state["icon"]:
            threading.Thread(target=window.hide, daemon=True).start()
            if not state.get("told"):
                state["told"] = True
                state["icon"].notify("Clipforge is still running in the tray, so the web app keeps working. "
                                     "Right-click the icon and choose Quit to stop it.", "Clipforge")
            return False
        if busy():
            return window.create_confirmation_dialog(
                "Clipforge is still working", "Downloads, reels or exports are still running and will stop. Close anyway?")
        return True

    window.events.closing += closing
    webview.start(boot, gui="edgechromium", private_mode=False,
                  storage_path=os.path.join(app_dir(), "WebView"))
    stop()


if __name__ == "__main__":
    main()
