"""
Social hub: connected accounts, captions and thumbnails for posts, and the
content calendar.

    crypto      tokens are encrypted at rest (Windows DPAPI, or Fernet elsewhere)
    networks    each network's sign-in settings, limits and profile lookup
    accounts    the connected accounts (one user can connect many per network)
    oauth       the sign-in flow: a one-time local page catches the network's redirect
    captions    per-network captions from the clip (local or cloud AI)
    thumbnail   thumbnails from the clip's own frames
    posts       a post = one clip sent to many accounts, scheduled on the calendar

Uploading itself is done by the publishers in recaply/publish and the
existing publish scheduler.
"""
