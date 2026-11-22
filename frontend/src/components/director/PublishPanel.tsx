import { AlertCircle, CheckCircle2, Loader2, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import type { Clip } from "@/lib/types";

interface Platform {
  key: string;
  label: string;
  connected: boolean;
  note?: string;
}

/** Upload a finished short to a connected platform now, or schedule it (the Windows app uploads while it runs). */
export function PublishPanel({ jobId, file, clip }: { jobId: string; file: string; clip: Clip }) {
  const [platforms, setPlatforms] = useState<Platform[] | null>(null);
  const [platform, setPlatform] = useState("");
  const [title, setTitle] = useState(clip.title || "");
  const [desc, setDesc] = useState(clip.description || "");
  const [tags, setTags] = useState((clip.hashtags ?? []).join(" "));
  const [privacy, setPrivacy] = useState("public");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api<{ platforms: Platform[] }>("/api/publish")
      .then((d) => {
        setPlatforms(d.platforms);
        setPlatform((p) => p || d.platforms.find((x) => x.connected)?.key || "");
      })
      .catch(() => setPlatforms([]));
  }, []);

  if (platforms === null) return null;
  const ready = platforms.filter((p) => p.connected);
  const missing = platforms.filter((p) => !p.connected);

  const send = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await api("/api/publish", { json: { job: jobId, file, platform, title, description: desc, tags, privacy, when } });
      setMsg({ ok: true, text: when ? "Scheduled. It uploads while Clipforge is running." : "Queued: uploading now. Follow it in the Queue." });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "That didn't work." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border bg-white/[.03] p-3">
      <p className="flex items-center gap-2 text-sm font-semibold"><Send className="size-4 text-primary" /> Publish</p>
      {ready.length ? (
        <>
          <Select value={platform} onChange={(e) => setPlatform(e.target.value)} aria-label="Platform">
            {ready.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </Select>
          <Input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} placeholder="Title" aria-label="Title" />
          <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} placeholder="Description" aria-label="Description" className="w-full rounded-lg border border-input bg-white/[.03] p-3 text-sm outline-none focus-visible:border-primary/60" />
          <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Hashtags, space separated" aria-label="Hashtags" />
          <Select value={privacy} onChange={(e) => setPrivacy(e.target.value)} aria-label="Privacy">
            <option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option>
          </Select>
          <div>
            <Label>Publish at (empty = now)</Label>
            <Input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} className="[color-scheme:dark]" />
          </div>
          <Button className="w-full" disabled={busy || !platform || !title.trim()} onClick={send}>
            {busy ? <Loader2 className="animate-spin" /> : <Send />} {when ? "Schedule" : "Publish now"}
          </Button>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">No platform is connected yet.</p>
      )}
      {msg && (
        <p role="status" className={`flex items-start gap-2 text-sm font-medium ${msg.ok ? "text-emerald-400" : "text-destructive"}`}>
          {msg.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> : <AlertCircle className="mt-0.5 size-4 shrink-0" />} {msg.text}
          {msg.ok && <Link to="/queue" className="ml-1 underline">Open queue</Link>}
        </p>
      )}
      {missing.map((p) => <p key={p.key} className="text-xs text-muted-foreground">{p.label}: {p.note}</p>)}
      <p className="text-xs text-muted-foreground">Only publish clips you own or have permission to use, and follow each platform's rules.</p>
    </div>
  );
}
