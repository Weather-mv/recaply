import { AudioLines, Loader2, Pause, Play, Scissors } from "lucide-react";
import { useState } from "react";
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
            <div className="text-center">
              <AudioLines className="mx-auto mb-2 size-12" />
              <p className="text-base font-semibold">Audio preview</p>
              <p className="text-sm text-white/60">Cut the clip to see the picture too.</p>
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
              {cut?.state === "failed" && <p className="text-sm text-destructive">{cut.error || "The cut failed."}</p>}
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
        {media && !player.playing && player.ready && (
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
