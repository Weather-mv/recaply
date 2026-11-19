"""
Live-stream mode (Phase 4): watch channels while they stream, catch the
big moments as they happen and turn each one into a clip.

    recorder   streamlink / yt-dlp -> ffmpeg -> rolling 5-minute .ts segments
    detect     per-second audio features + live chat -> fused score -> spikes
    chat       Twitch IRC (anonymous, read-only) and YouTube live chat
    channel    one channel's loop: wait for live, record, detect, cut, auto-stop
    pipeline   a cut clip -> a normal clip job (analyze, optionally shorts) -> notify
    notify     in-app events, a generic webhook, a Discord webhook
    manager    many channels at once, each with its own config

Everything runs on the user's own machine, like the rest of Recaply.
"""
