import { AudioLines, Loader2, Pause, Play, Scissors } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type { MediaSel, Player } from "@/hooks/usePlayer";
import { api, ApiError } from "@/lib/api";
import { BEAT_META } from "@/lib/beats";
import type { Beat, Clip, Job } from "@/lib/types";
import { cn, fmtPrecise, fmtTime } from "@/lib/utils";

interface Props {
  job: Job;
  clip: Clip;
  media: MediaSel | null;
  player: Player;
  beat: Beat | null;
  onChanged: () => void;
}

/** The preview: the cut clip, the source video, or just the audio, with the current story beat on top. */
export function VideoStage({ job, clip, media, player, beat, onChanged }: Props) {
  const [err, setErr] = useState("");
  const cut = job.cuts[String(clip.id)];
  const cutting = cut && (cut.state === "queued" || cut.state === "cutting");

  const startCut = async () => {
    setErr("");
    try {
      await api(`/api/clipjob/${job.id}/cut`, { json: { ids: [clip.id], quality: "720" } });
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Couldn't start the cut.");
    }
  };

  // A cut error is ffmpeg's raw output (long stream URLs); say it in a line instead.
  const cutError = (e?: string | null) => {
    const t = e || "";
    if (/403|Forbidden/i.test(t)) return "YouTube refused the download this time. Try again.";
    if (/getaddrinfo|resolve|network|timed? ?out/i.test(t)) return "Couldn't reach the video site. Check the internet connection and try again.";
    return t ? "The video couldn't be fetched. Try again." : "";
  };

  // Links are analysed from their audio only, so the picture isn't on disk yet: fetch the clip (720p) the first
  // time it's opened. A cut that failed earlier (an old visit) is retried once; a clip is tried once per visit.
  const tried = useRef(new Set<string>());
  useEffect(() => {
    const key = `${job.id}:${clip.id}`;
    if (job.state !== "ready" || job.media.source || (cut && cut.state !== "failed") || tried.current.has(key)) return;
    tried.current.add(key);
    void startCut();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.id, job.state, job.media.source, clip.id, cut]);

  return (
    <div className="space-y-2">
      <div className="group relative aspect-video overflow-hidden rounded-lg border bg-black">
        {media && (
          <video
            ref={player.ref}
            src={media.url}
            playsInline
            preload="metadata"
            onClick={player.toggle}
            className={cn("size-full object-contain", !media.video && "hidden")}
          />
        )}

        {media && !media.video && (
          <div className="grid size-full place-items-center bg-gradient-to-br from-primary/20 to-black text-white/70">
            <div className="max-w-md px-6 text-center">
              <button type="button" onClick={player.toggle} disabled={!player.ready} aria-label={player.playing ? "Pause" : "Play"}
                className="mx-auto mb-4 grid size-16 place-items-center rounded-full bg-primary text-primary-foreground shadow-xl transition-transform hover:scale-105 disabled:opacity-60">
                {player.playing ? <Pause className="size-7" /> : <Play className="size-7 translate-x-0.5" />}
              </button>
              <p className="flex items-center justify-center gap-2 text-base font-semibold">
                {cutting ? <Loader2 className="size-5 animate-spin" /> : <AudioLines className="size-5" />}
                {cutting ? "Getting the video…" : "Audio preview"}
              </p>
              <p className="text-sm text-white/60">{cutting ? "You can listen now; the picture appears here in a moment." : cut?.state === "failed" ? cutError(cut.error) : "Cut the clip to see the picture too."}</p>
              {!cutting && <Button size="sm" className="mt-3" onClick={startCut}><Scissors /> {cut?.state === "failed" ? "Try again" : "Get the video"}</Button>}
            </div>
          </div>
        )}

        {!media && (
          <div className="grid size-full place-items-center p-6 text-center text-white/80">
            <div className="max-w-sm space-y-3">
              <p className="text-lg font-semibold">No preview yet</p>
              <p className="text-sm text-white/60">Links aren't downloaded in full. Cut this clip to watch it here and edit it in the Director.</p>
              <Button onClick={startCut} disabled={!!cutting}>
                {cutting ? <Loader2 className="animate-spin" /> : <Scissors />}
                {cutting ? "Cutting…" : "Cut this clip"}
              </Button>
              {cut?.state === "failed" && <p className="text-sm text-destructive">{cutError(cut.error)}</p>}
              {err && <p className="text-sm text-destructive">{err}</p>}
            </div>
          </div>
        )}

        {media && beat && (
          <span className={cn("pointer-events-none absolute left-3 top-3 rounded-full px-3 py-1 text-sm font-bold text-white shadow", BEAT_META[beat.type].bar)}>
            {BEAT_META[beat.type].label}
          </span>
        )}
        {media && player.time < 3 && clip.hook && (
          <span className="pointer-events-none absolute inset-x-6 top-12 text-center text-2xl font-extrabold text-white drop-shadow-[0_2px_8px_rgba(0,0,0,.9)]">{clip.hook}</span>
        )}
        {media?.video && !player.playing && player.ready && (
          <button type="button" onClick={player.toggle} aria-label="Play" className="absolute inset-0 grid place-items-center bg-black/10 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100">
            <span className="grid size-16 place-items-center rounded-full bg-primary text-primary-foreground shadow-xl"><Play className="size-7 translate-x-0.5" /></span>
          </button>
        )}
      </div>

      <div className="flex items-center gap-3">
        <Button size="icon" variant="secondary" onClick={player.toggle} disabled={!media || !player.ready} aria-label={player.playing ? "Pause" : "Play"}>
          {player.playing ? <Pause /> : <Play />}
        </Button>
        <span className="tabular text-sm font-semibold">
          {fmtPrecise(player.time)} <span className="text-muted-foreground">/ {fmtTime(clip.duration)}</span>
        </span>
        <span className="ml-auto text-sm text-muted-foreground">
          {media ? (media.kind === "cut" ? "Cut clip" : media.kind === "source" ? "Source video" : "Audio only") : "No preview"} · Space plays, arrows step
        </span>
      </div>
    </div>
  );
}
