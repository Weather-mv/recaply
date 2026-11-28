"""
webutil.py — response headers shared by the Windows app's local server and the
online service: security headers, a Content-Security-Policy on pages, caching
rules and gzip.
"""

import gzip

from flask import request

GZIP_TYPES = ("text/html", "application/json", "text/plain", "text/css", "text/javascript", "application/javascript")


def csp(media="", connect=""):
    """Inline scripts and styles are part of the pages, so they stay allowed; everything
    else must come from this site (plus Google Fonts, YouTube thumbnails and players)."""
    return ("default-src 'self'; img-src 'self' data: blob: https://i.ytimg.com https://static-cdn.jtvnw.net https://yt3.ggpht.com https://*.googleusercontent.com https://media.licdn.com https://pbs.twimg.com https://*.cdninstagram.com https://*.fbcdn.net; "
            f"media-src 'self' blob: {media}; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; "
            f"script-src 'self' 'unsafe-inline'; connect-src 'self' {connect}; "
            "frame-src https://www.youtube-nocookie.com https://player.twitch.tv; frame-ancestors 'self'; "
            "base-uri 'self'; form-action 'self' https://checkout.stripe.com https://billing.stripe.com; object-src 'none'")


_gz = {}            # (path, etag) -> gzipped body of a static CSS / JS file, made once


def _gzip_static(resp):
    """CSS and JS shrink ~4x with gzip; compress each version of a file once and reuse it."""
    key = (request.path, resp.headers.get("ETag"))
    body = _gz.get(key)
    if body is None:
        resp.direct_passthrough = False
        body = gzip.compress(resp.get_data(), compresslevel=9)
        if len(_gz) > 200:
            _gz.clear()
        _gz[key] = body
    else:
        resp.response.close()                  # the file Flask opened isn't needed
    resp.set_data(body)
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


def install(app, policy, signed_in):
    """Add the shared after-request handler. `signed_in()` says whether pages are private."""

    @app.after_request
    def finish_response(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if resp.mimetype == "text/html":
            resp.headers.setdefault("Content-Security-Policy", policy)
            resp.headers.setdefault("Cache-Control", "no-store" if signed_in() else "no-cache")
        if request.path.startswith("/api/") and resp.mimetype == "application/json":
            resp.headers["Cache-Control"] = "no-store"
        elif request.path.startswith("/static/"):
            # Pages link static files with ?v=<newest file time>, so a cached copy is never stale.
            resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if request.args.get("v")
                                             else "public, max-age=300")
        if (request.path.startswith("/static/") and resp.status_code == 200 and resp.direct_passthrough
                and resp.mimetype in GZIP_TYPES and "gzip" in request.headers.get("Accept-Encoding", "")):
            return _gzip_static(resp)
        # The status JSON is polled every few seconds and shrinks ~7x with gzip.
        if (resp.status_code == 200 and not resp.direct_passthrough and resp.mimetype in GZIP_TYPES
                and "gzip" in request.headers.get("Accept-Encoding", "")
                and "Content-Encoding" not in resp.headers):
            data = resp.get_data()
            if len(data) > 1024:
                resp.set_data(gzip.compress(data, compresslevel=5))
                resp.headers["Content-Encoding"] = "gzip"
                resp.headers["Vary"] = "Accept-Encoding"
        return resp

    return finish_response
