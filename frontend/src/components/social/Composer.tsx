import { AlertTriangle, CalendarClock, Check, ImagePlus, Loader2, Send, Sparkles, Upload, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { api, ApiError, clipFileUrl } from "@/lib/api";
import {
  counterTone, NET_LOOK, THUMB_SIZE, thumbFileUrl, toLocalInput,
  type NetworkInfo, type NetworkKey, type PostableVideo, type SocialAccount, type SocialPost,
} from "@/lib/social";
import { cn } from "@/lib/utils";
import { Avatar } from "./AccountsPanel";

export interface ComposerSeed {
  post?: SocialPost;                                  // edit this post
  job?: string;
  clip?: number;
  file?: string;
  day?: Date;                                         // "+" on a calendar day
}

interface Frame {
  at: number;
  name: string;
}
interface Copy {
  title: string;
  caption: string;
  edited: boolean;
}
const TONES = ["hype", "casual", "professional", "funny", "informative"];
const TEMPLATES = [
  { key: "bold", label: "Bold" },
  { key: "boxed", label: "Boxed" },
  { key: "clean", label: "No text" },
];

const csrf = (): string => document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "";

function defaultTime(day?: Date): string {
  const d = day ? new Date(day) : new Date(Date.now() + 3600_000);
  if (day) d.setHours(18, 0, 0, 0);
  else d.setMinutes(0, 0, 0);
  return toLocalInput(d.getTime() / 1000);
}

/** Write a post once, tune it per network, pick a thumbnail, then post now, schedule or save a draft. */
export function Composer({ seed, networks, accounts, onClose, onSaved }: {
  seed: ComposerSeed;
  networks: NetworkInfo[];
  accounts: SocialAccount[];
  onClose: () => void;
  onSaved: (text: string) => void;
}) {
  const editing = seed.post;
  const netInfo = useMemo(() => Object.fromEntries(networks.map((n) => [n.key, n])) as Record<NetworkKey, NetworkInfo>, [networks]);
  const [videos, setVideos] = useState<PostableVideo[] | null>(null);
  const [video, setVideo] = useState<PostableVideo | null>(null);
  const [picked, setPicked] = useState<number[]>(() => editing?.targets.map((t) => t.account_id) ?? accounts.filter((a) => a.status === "ok").slice(0, 1).map((a) => a.id));
  const [shared, setShared] = useState<Copy>({ title: editing?.title ?? "", caption: editing?.targets[0]?.caption ?? "", edited: false });
  const [per, setPer] = useState<Partial<Record<NetworkKey, Copy>>>(() => {
    const out: Partial<Record<NetworkKey, Copy>> = {};
    editing?.targets.forEach((t) => { out[t.platform] = { title: t.title, caption: t.caption, edited: true }; });
    return out;
  });
  const [tab, setTab] = useState<"all" | NetworkKey>("all");
  const [tone, setTone] = useState("hype");
  const [genBusy, setGenBusy] = useState(false);
  const [genNote, setGenNote] = useState("");
  const [frames, setFrames] = useState<Frame[]>([]);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [thumbText, setThumbText] = useState("");
  const [template, setTemplate] = useState("bold");
  const [thumbs, setThumbs] = useState<Record<string, string>>(() => {
    const t = editing?.targets.find((x) => x.thumbnail);
    const out: Record<string, string> = {};
    if (t?.thumbnail) out.custom = t.thumbnail;
    return out;
  });
  const [thumbBusy, setThumbBusy] = useState(false);
  const [mode, setMode] = useState<"now" | "schedule" | "draft">(editing ? (editing.status === "draft" ? "draft" : "schedule") : seed.day ? "schedule" : "now");
  const [when, setWhen] = useState(editing ? toLocalInput(editing.run_at) : defaultTime(seed.day));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  // the videos that can be posted; preselect the seeded / edited one
  useEffect(() => {
    api<{ videos: PostableVideo[] }>("/api/social/videos")
      .then((d) => {
        setVideos(d.videos);
        const job = editing?.job_id ?? seed.job;
        const file = editing?.file ?? seed.file;
        const clip = editing?.clip_id ?? seed.clip;
        const v = d.videos.find((x) => x.job_id === job && (file ? x.file === file : x.clip_id === clip)) ?? null;
        if (v) choose(v, !editing);
      })
      .catch(() => setVideos([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const chosen = accounts.filter((a) => picked.includes(a.id));
  const nets = [...new Set(chosen.map((a) => a.network))] as NetworkKey[];

  function choose(v: PostableVideo, fill: boolean) {
    setVideo(v);
    if (fill) {
      const tags = (v.hashtags ?? []).map((t) => (t.startsWith("#") ? t : `#${t}`)).join(" ");
      const body = [v.hook && v.hook !== v.title ? v.hook : "", v.description].filter(Boolean).join("\n\n");
      setShared({ title: v.title, caption: [body || v.title, tags].filter(Boolean).join("\n\n"), edited: false });
      setPer({});
      setThumbText(v.title);
    }
    setFrames([]);
    setFrame(null);
    api<{ frames: Frame[] }>("/api/social/thumbnail/frames", { json: { job_id: v.job_id, clip_id: v.clip_id, file: v.file } })
      .then((d) => { setFrames(d.frames); setFrame((f) => f ?? d.frames[0] ?? null); })
      .catch(() => setFrames([]));
  }

  const copyFor = (n: NetworkKey): Copy => per[n] ?? shared;
  const setCopy = (n: "all" | NetworkKey, patch: Partial<Copy>) => {
    if (n === "all") setShared((s) => ({ ...s, ...patch }));
    else setPer((p) => ({ ...p, [n]: { ...copyFor(n), ...patch, edited: true } }));
  };

  const generate = async () => {
    if (!video || !nets.length) return;
    setGenBusy(true);
    setGenNote("");
    try {
      const r = await api<{ captions: Record<NetworkKey, { title: string; caption: string }>; by: string }>("/api/social/captions", {
        json: { job_id: video.job_id, clip_id: video.clip_id, networks: nets, tone },
      });
      const next: Partial<Record<NetworkKey, Copy>> = { ...per };
      for (const n of nets) if (r.captions[n]) next[n] = { title: r.captions[n].title, caption: r.captions[n].caption, edited: true };
      setPer(next);
      setTab(nets[0]);
      setGenNote(r.by.startsWith("ai:") ? `Written by ${r.by.slice(3)} for each network. Edit anything you like.` : "No AI is available, so the clip's own title and description were used.");
    } catch (e) {
      setGenNote(e instanceof ApiError ? e.message : "Couldn't write captions.");
    } finally {
      setGenBusy(false);
    }
  };

  const makeThumbs = async () => {
    if (!video || !frame) return;
    setThumbBusy(true);
    try {
      const sizes = [...new Set((nets.length ? nets : (["youtube"] as NetworkKey[])).map((n) => THUMB_SIZE[n]))];
      const out: Record<string, string> = {};
      for (const size of sizes) {
        const r = await api<{ name: string }>("/api/social/thumbnail", {
          json: { job_id: video.job_id, clip_id: video.clip_id, frame: frame.name, text: thumbText, size, template },
        });
        out[size] = r.name;
      }
      setThumbs(out);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Couldn't make the thumbnail.");
    } finally {
      setThumbBusy(false);
    }
  };

  const upload = async (f: File) => {
    if (!video) return;
    setThumbBusy(true);
    try {
      const fd = new FormData();
      fd.append("job_id", video.job_id);
      fd.append("clip_id", String(video.clip_id));
      fd.append("image", f);
      const res = await fetch("/api/social/thumbnail/upload", { method: "POST", body: fd, headers: { "X-CSRF-Token": csrf() }, credentials: "same-origin" });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Upload failed.");
      setThumbs({ custom: d.name });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setThumbBusy(false);
    }
  };

  const thumbFor = (n: NetworkKey): string | null => thumbs.custom ?? thumbs[THUMB_SIZE[n]] ?? null;
  const preview = thumbs.custom ?? thumbs.youtube ?? Object.values(thumbs)[0];

  const problems: string[] = [];
  if (!video) problems.push("Pick a video.");
  if (!chosen.length) problems.push("Pick at least one account.");
  for (const n of nets) {
    const c = copyFor(n);
    const info = netInfo[n];
    if (info && c.caption.length > info.caption_max) problems.push(`${NET_LOOK[n].label} caption is too long.`);
    if (info?.title_max && c.title.length > info.title_max) problems.push(`${NET_LOOK[n].label} title is too long.`);
  }

  const save = async () => {
    if (problems.length || !video) return;
    setBusy(true);
    setErr("");
    const whenSec = mode === "schedule" ? new Date(when).getTime() / 1000 : undefined;
    try {
      if (editing) {
        await api(`/api/social/posts/${editing.group}`, {
          method: "PATCH",
          json: {
            when: mode === "now" ? "now" : whenSec ?? editing.run_at, draft: mode === "draft", thumb_at: frame?.at ?? null,
            targets: editing.targets.map((t) => ({ id: t.id, title: copyFor(t.platform).title, caption: copyFor(t.platform).caption, thumbnail: thumbFor(t.platform), privacy: t.privacy })),
          },
        });
        onSaved("Post updated.");
      } else {
        await api("/api/social/posts", {
          json: {
            job_id: video.job_id, clip_id: video.clip_id, file: video.file, draft: mode === "draft",
            when: mode === "now" ? "now" : mode === "schedule" ? whenSec : null, thumb_at: frame?.at ?? null,
            targets: chosen.map((a) => ({ account_id: a.id, title: copyFor(a.network).title, caption: copyFor(a.network).caption, thumbnail: thumbFor(a.network) })),
          },
        });
        onSaved(mode === "now" ? "Posting now. Follow it on the calendar." : mode === "draft" ? "Saved as a draft." : "Scheduled. Clipforge posts it while it's running.");
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  const active = tab === "all" ? shared : copyFor(tab);
  const activeInfo = tab === "all" ? null : netInfo[tab];
  const limit = tab === "all" ? Math.min(...(nets.length ? nets.map((n) => netInfo[n]?.caption_max ?? 2200) : [2200])) : activeInfo?.caption_max ?? 2200;
  const titleMax = tab === "all" ? Math.max(0, ...nets.map((n) => netInfo[n]?.title_max ?? 0)) : activeInfo?.title_max ?? 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-[2px]" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label={editing ? "Edit post" : "New post"} className="flex h-full w-full max-w-[1080px] flex-col bg-background shadow-2xl">
        <header className="flex items-center gap-3 border-b px-5 py-4">
          <h2 className="flex-1 text-lg font-semibold">{editing ? "Edit post" : "New post"}</h2>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}><X /></Button>
        </header>

        <div className="grid flex-1 gap-6 overflow-y-auto p-5 lg:grid-cols-[1fr_340px]">
          <div className="min-w-0 space-y-6">
            {/* 1. video */}
            <section>
              <Label className="text-[15px]">Video</Label>
              {videos === null ? (
                <p className="mt-2 flex items-center gap-2 text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading your clips…</p>
              ) : videos.length === 0 ? (
                <p className="mt-2 text-muted-foreground">No finished clips yet. <Link to="/" className="text-primary hover:underline">Make some clips</Link> first.</p>
              ) : (
                <Select className="mt-2" disabled={!!editing} value={video ? `${video.job_id}|${video.file}` : ""}
                  onChange={(e) => { const v = videos.find((x) => `${x.job_id}|${x.file}` === e.target.value); if (v) choose(v, true); }}>
                  <option value="" disabled>Choose a clip…</option>
                  {videos.map((v) => (
                    <option key={`${v.job_id}|${v.file}`} value={`${v.job_id}|${v.file}`}>
                      {v.title || `Clip ${v.clip_id}`} · {v.kind === "short" ? `Short ${v.format}` : "Cut"} · {Math.round(v.duration)}s · {v.job_title.slice(0, 40)}
                    </option>
                  ))}
                </Select>
              )}
            </section>

            {/* 2. accounts */}
            <section>
              <Label className="text-[15px]">Post to</Label>
              {accounts.length === 0 ? (
                <p className="mt-2 text-muted-foreground">No accounts connected yet. Connect one on the <Link to="/social/accounts" className="text-primary hover:underline">Accounts</Link> tab.</p>
              ) : (
                <div className="mt-2 flex flex-wrap gap-2">
                  {accounts.map((a) => {
                    const on = picked.includes(a.id);
                    const off = a.status === "reconnect" || (!!editing && !on);
                    return (
                      <button key={a.id} type="button" disabled={off || !!editing}
                        onClick={() => setPicked((p) => (on ? p.filter((x) => x !== a.id) : [...p, a.id]))}
                        className={cn("flex items-center gap-2 rounded-full py-1 pl-1 pr-3 text-[15px] ring-1 ring-inset transition-colors disabled:opacity-50",
                          on ? "bg-primary/10 ring-primary" : "ring-border hover:bg-secondary")}>
                        <Avatar acc={a} size={28} />
                        <span className="max-w-[160px] truncate">{a.name}</span>
                        {on && <Check className="size-4 text-primary" />}
                      </button>
                    );
                  })}
                </div>
              )}
            </section>

            {/* 3. captions */}
            <section>
              <div className="flex flex-wrap items-center gap-2">
                <Label className="flex-1 text-[15px]">Caption</Label>
                <Select aria-label="Tone" className="h-9 w-auto" value={tone} onChange={(e) => setTone(e.target.value)}>
                  {TONES.map((t) => <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}
                </Select>
                <Button size="sm" variant="secondary" disabled={!video || !nets.length || genBusy} onClick={generate}>
                  {genBusy ? <Loader2 className="animate-spin" /> : <Sparkles />} Write with AI
                </Button>
              </div>
              {genNote && <p className="mt-2 text-sm text-muted-foreground">{genNote}</p>}
              <div className="mt-3 flex flex-wrap gap-1 border-b" role="tablist">
                {(["all", ...nets] as ("all" | NetworkKey)[]).map((k) => {
                  const Icon = k === "all" ? null : NET_LOOK[k].icon;
                  return (
                    <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                      className={cn("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[15px]", tab === k ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
                      {Icon && <Icon className="size-4" />} {k === "all" ? "All networks" : NET_LOOK[k].label}
                      {k !== "all" && per[k]?.edited && <span className="size-1.5 rounded-full bg-primary" title="Edited for this network" />}
                    </button>
                  );
                })}
              </div>
              <div className="mt-3 space-y-3">
                {titleMax > 0 && (
                  <div>
                    <div className="flex justify-between text-sm"><span className="text-muted-foreground">Title {tab === "all" ? "(YouTube, LinkedIn)" : ""}</span>
                      <span className={counterTone(active.title.length, titleMax)}>{active.title.length}/{titleMax}</span></div>
                    <Input className="mt-1" value={active.title} onChange={(e) => setCopy(tab, { title: e.target.value })} />
                  </div>
                )}
                <div>
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">{tab === "all" ? "Used for every network you haven't edited separately" : `Only for ${NET_LOOK[tab].label}`}</span>
                    <span className={counterTone(active.caption.length, limit)}>{active.caption.length}/{limit}</span>
                  </div>
                  <textarea value={active.caption} onChange={(e) => setCopy(tab, { caption: e.target.value })} rows={tab === "x" ? 4 : 8}
                    className="mt-1 w-full resize-y rounded-lg border border-input bg-transparent px-3 py-2 text-[15px] leading-relaxed outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                  {tab !== "all" && per[tab]?.edited && (
                    <button className="text-sm text-primary hover:underline" onClick={() => setPer((p) => { const n = { ...p }; delete n[tab]; return n; })}>Use the shared caption again</button>
                  )}
                </div>
              </div>
            </section>

            {/* 4. thumbnail */}
            <section>
              <Label className="text-[15px]">Thumbnail</Label>
              {!video ? <p className="mt-2 text-muted-foreground">Pick a video first.</p> : (
                <div className="mt-2 space-y-3">
                  <div className="flex gap-2 overflow-x-auto pb-1">
                    {frames.length === 0 && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Reading frames…</p>}
                    {frames.map((f) => (
                      <button key={f.name} onClick={() => setFrame(f)} aria-label={`Frame at ${f.at.toFixed(1)} seconds`}
                        className={cn("relative h-28 shrink-0 overflow-hidden rounded-lg ring-2 transition", frame?.name === f.name ? "ring-primary" : "ring-transparent opacity-80 hover:opacity-100")}>
                        <img src={thumbFileUrl(video.job_id, f.name)} alt="" className="h-full w-auto" />
                        <span className="absolute bottom-1 left-1 rounded bg-black/70 px-1.5 text-xs text-white">{f.at.toFixed(1)}s</span>
                      </button>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-end gap-2">
                    <div className="min-w-[200px] flex-1">
                      <span className="text-sm text-muted-foreground">Text on the thumbnail</span>
                      <Input className="mt-1" value={thumbText} maxLength={80} onChange={(e) => setThumbText(e.target.value)} />
                    </div>
                    <Select aria-label="Style" className="w-auto" value={template} onChange={(e) => setTemplate(e.target.value)}>
                      {TEMPLATES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                    </Select>
                    <Button variant="secondary" disabled={!frame || thumbBusy} onClick={makeThumbs}>
                      {thumbBusy ? <Loader2 className="animate-spin" /> : <ImagePlus />} Make thumbnail
                    </Button>
                    <Button variant="ghost" disabled={thumbBusy} onClick={() => fileRef.current?.click()}><Upload /> Upload</Button>
                    <input ref={fileRef} type="file" accept="image/png,image/jpeg" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
                  </div>
                  {Object.keys(thumbs).length > 0 && (
                    <div className="flex flex-wrap items-start gap-3">
                      {Object.entries(thumbs).map(([size, name]) => (
                        <figure key={size} className="space-y-1">
                          <img src={thumbFileUrl(video.job_id, name)} alt={`${size} thumbnail`} className={cn("rounded-lg ring-1 ring-border", size === "vertical" ? "h-44" : "h-28")} />
                          <figcaption className="text-xs capitalize text-muted-foreground">{size === "custom" ? "Your image" : size}</figcaption>
                        </figure>
                      ))}
                      <button className="text-sm text-muted-foreground hover:text-foreground" onClick={() => setThumbs({})}>Remove</button>
                    </div>
                  )}
                  {nets.includes("instagram") && <p className="text-sm text-muted-foreground">Instagram uses the selected frame as the Reel's cover. X doesn't take a thumbnail.</p>}
                </div>
              )}
            </section>
          </div>

          {/* preview + when */}
          <aside className="space-y-4 lg:sticky lg:top-0 lg:self-start">
            <div className="surface overflow-hidden rounded-xl">
              {video ? (
                <div className="relative bg-black">
                  {preview ? <img src={thumbFileUrl(video.job_id, preview)} alt="" className="mx-auto max-h-[300px]" />
                    : <video src={clipFileUrl(video.job_id, video.file)} className="mx-auto max-h-[300px]" muted controls preload="metadata" />}
                </div>
              ) : <div className="grid h-40 place-items-center text-muted-foreground">Preview</div>}
              <div className="space-y-3 p-4">
                {(chosen.length ? chosen : []).slice(0, 4).map((a) => (
                  <div key={a.id} className="flex gap-2.5">
                    <Avatar acc={a} size={30} />
                    <div className="min-w-0 flex-1 text-sm">
                      <p className="font-medium">{a.name}</p>
                      {netInfo[a.network]?.title_max ? <p className="line-clamp-1 font-semibold">{copyFor(a.network).title}</p> : null}
                      <p className="line-clamp-3 whitespace-pre-line text-muted-foreground">{copyFor(a.network).caption}</p>
                    </div>
                  </div>
                ))}
                {!chosen.length && <p className="text-sm text-muted-foreground">Pick accounts to see how the post looks.</p>}
              </div>
            </div>

            <div className="surface space-y-3 rounded-xl p-4">
              <div className="grid grid-cols-3 gap-1 rounded-lg bg-secondary p-1 text-sm">
                {([["now", "Post now"], ["schedule", "Schedule"], ["draft", "Draft"]] as const).map(([k, l]) => (
                  <button key={k} onClick={() => setMode(k)} className={cn("rounded-md py-1.5 font-medium", mode === k ? "bg-card shadow-sm" : "text-muted-foreground")}>{l}</button>
                ))}
              </div>
              {mode === "schedule" && (
                <label className="block text-sm">
                  <span className="text-muted-foreground">Date and time</span>
                  <Input type="datetime-local" className="mt-1" value={when} onChange={(e) => setWhen(e.target.value)} />
                </label>
              )}
              {mode !== "draft" && <p className="text-sm text-muted-foreground">Clipforge must be running to post. A post missed while it was closed goes out when it opens again.</p>}
              {(problems.length > 0 || err) && (
                <div className="flex gap-2 rounded-lg bg-red-500/10 p-3 text-sm text-red-600 dark:text-red-400">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  <div>{err && <p>{err}</p>}{problems.map((p) => <p key={p}>{p}</p>)}</div>
                </div>
              )}
              <Button className="w-full" size="lg" disabled={busy || problems.length > 0} onClick={save}>
                {busy ? <Loader2 className="animate-spin" /> : mode === "schedule" ? <CalendarClock /> : <Send />}
                {editing ? "Save changes" : mode === "now" ? "Post now" : mode === "schedule" ? "Schedule post" : "Save draft"}
              </Button>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
