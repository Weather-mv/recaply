import { AlertCircle, CheckCircle2, Loader2, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";

interface Kit {
  logo: boolean;
  logo_pos: string;
  logo_size: number;
  logo_opacity: number;
  accent: string;
  text_color: string;
  highlight_color: string;
  keyword_color: string;
  caption_style: string;
  font: string;
  intro: boolean;
  outro: boolean;
}
interface BrandReply {
  kit: Kit;
  fonts: { key: string; label: string }[];
  positions: string[];
  captions: { key: string; label: string }[];
}

const POS: Record<string, string> = { tl: "Top left", tr: "Top right", bl: "Bottom left", br: "Bottom right" };
type Media = "logo" | "font" | "intro" | "outro";
const ACCEPT: Record<Media, string> = { logo: ".png,.jpg,.jpeg,.webp", font: ".ttf,.otf", intro: ".mp4,.mov,.webm,.mkv", outro: ".mp4,.mov,.webm,.mkv" };

const Panel = ({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) => (
  <section className="surface space-y-4 rounded-2xl p-5">
    <div>
      <h2 className="text-sm font-semibold">{title}</h2>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
    {children}
  </section>
);

function Color({ label, value, onChange, optional }: { label: string; value: string; onChange: (v: string) => void; optional?: boolean }) {
  return (
    <label className="flex items-center gap-3 rounded-lg border bg-white/[.03] p-2.5 text-sm">
      <input type="color" value={/^#[0-9a-f]{6}$/i.test(value) ? value : "#ffffff"} onChange={(e) => onChange(e.target.value)} aria-label={label} className="size-9 shrink-0 cursor-pointer rounded-md border-0 bg-transparent p-0" />
      <span className="min-w-0 flex-1">
        <span className="block font-medium">{label}</span>
        <span className="block font-mono text-xs text-muted-foreground">{value || (optional ? "caption style's own" : "")}</span>
      </span>
      {optional && value && <button type="button" onClick={() => onChange("")} className="text-xs text-muted-foreground hover:text-foreground">Reset</button>}
    </label>
  );
}

function MediaSlot({ which, label, hint, has, onChange, onError }: { which: Media; label: string; hint: string; has: boolean; onChange: () => void; onError: (m: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const upload = async (f: File) => {
    setBusy(true);
    const form = new FormData();
    form.set("file", f);
    try {
      const res = await fetch(`/api/brand/${which}`, {
        method: "POST",
        body: form,
        credentials: "same-origin",
        headers: { "X-CSRF-Token": document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(data.error ?? "Upload failed.", res.status);
      onChange();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    try {
      await api(`/api/brand/${which}`, { method: "DELETE" });
      onChange();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "That didn't work.");
    }
  };
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-white/[.03] px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{has ? "Uploaded" : hint}</p>
      </div>
      <input ref={input} type="file" hidden accept={ACCEPT[which]} onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => input.current?.click()}>
        {busy ? <Loader2 className="animate-spin" /> : <Upload />} {has ? "Replace" : "Upload"}
      </Button>
      {has && <Button size="sm" variant="ghost" aria-label={`Remove ${label}`} className="px-2 text-muted-foreground hover:text-destructive" onClick={remove}><Trash2 /></Button>}
    </div>
  );
}

export default function BrandKit() {
  const [data, setData] = useState<BrandReply | null>(null);
  const [kit, setKit] = useState<Kit | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await api<BrandReply>("/api/brand");
      setData(d);
      setKit((k) => (k ? { ...k, logo: d.kit.logo, intro: d.kit.intro, outro: d.kit.outro, font: d.kit.font === "custom" ? "custom" : k.font } : d.kit));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't load your brand kit.");
    }
  }, []);
  useEffect(() => void load(), [load]);

  const set = <K extends keyof Kit>(k: K, v: Kit[K]) => {
    setKit((p) => (p ? { ...p, [k]: v } : p));
    setState("idle");
  };

  const save = async () => {
    if (!kit) return;
    setState("saving");
    setError("");
    try {
      const d = await api<BrandReply>("/api/brand", { json: kit });
      setData(d);
      setKit(d.kit);
      setState("saved");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save.");
      setState("idle");
    }
  };

  return (
    <AppShell crumbs={[{ label: "Brand Kit" }]}>
      <div className="mx-auto max-w-4xl space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Brand Kit</h1>
            <p className="mt-1 text-muted-foreground">Logo, colours, caption style and intro/outro. Applied to exports when "Use brand kit" is on in the Director.</p>
          </div>
          <Button onClick={save} disabled={!kit || state === "saving"}>
            {state === "saving" ? <Loader2 className="animate-spin" /> : state === "saved" ? <CheckCircle2 /> : null} {state === "saved" ? "Saved" : "Save changes"}
          </Button>
        </div>
        {error && <p role="alert" className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"><AlertCircle className="size-4" /> {error}</p>}
        {!kit || !data ? (
          !error && <div className="grid place-items-center py-16"><Loader2 className="size-7 animate-spin text-primary" /></div>
        ) : (
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
            <Panel title="Colours" hint="Used for the hook box and the caption words.">
              <div className="space-y-2">
                <Color label="Accent (hook box)" value={kit.accent} onChange={(v) => set("accent", v)} />
                <Color label="Caption text" value={kit.text_color} onChange={(v) => set("text_color", v)} />
                <Color label="Keyword highlight" value={kit.keyword_color} onChange={(v) => set("keyword_color", v)} />
                <Color label="Spoken-word highlight" value={kit.highlight_color} onChange={(v) => set("highlight_color", v)} optional />
              </div>
            </Panel>

            <Panel title="Captions" hint="The caption look used when you pick “Brand kit style”.">
              <div className="space-y-3">
                <label className="block text-sm">
                  <span className="mb-1.5 block text-muted-foreground">Style</span>
                  <Select value={kit.caption_style} onChange={(e) => set("caption_style", e.target.value)}>
                    {data.captions.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                  </Select>
                </label>
                <label className="block text-sm">
                  <span className="mb-1.5 block text-muted-foreground">Font</span>
                  <Select value={kit.font} onChange={(e) => set("font", e.target.value)}>
                    {data.fonts.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                    <option value="custom">My uploaded font</option>
                  </Select>
                </label>
                <MediaSlot which="font" label="Custom font" hint=".ttf or .otf" has={kit.font === "custom"} onChange={() => { void load(); }} onError={setError} />
              </div>
            </Panel>

            <Panel title="Logo / watermark">
              <div className="space-y-3">
                <MediaSlot which="logo" label="Logo" hint="PNG, JPG or WebP" has={kit.logo} onChange={() => void load()} onError={setError} />
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <label className="block">
                    <span className="mb-1.5 block text-muted-foreground">Position</span>
                    <Select value={kit.logo_pos} onChange={(e) => set("logo_pos", e.target.value)}>
                      {data.positions.map((p) => <option key={p} value={p}>{POS[p] ?? p}</option>)}
                    </Select>
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-muted-foreground">Size {Math.round(kit.logo_size * 100)}%</span>
                    <input type="range" min={0.05} max={0.4} step={0.01} value={kit.logo_size} onChange={(e) => set("logo_size", Number(e.target.value))} className="mt-2 w-full accent-[hsl(var(--primary))]" />
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-muted-foreground">Opacity {Math.round(kit.logo_opacity * 100)}%</span>
                    <input type="range" min={0.1} max={1} step={0.05} value={kit.logo_opacity} onChange={(e) => set("logo_opacity", Number(e.target.value))} className="mt-2 w-full accent-[hsl(var(--primary))]" />
                  </label>
                </div>
              </div>
            </Panel>

            <Panel title="Intro and outro" hint="Short clips added before and after every export.">
              <div className="space-y-2">
                <MediaSlot which="intro" label="Intro clip" hint="MP4, MOV or WebM" has={kit.intro} onChange={() => void load()} onError={setError} />
                <MediaSlot which="outro" label="Outro clip" hint="MP4, MOV or WebM" has={kit.outro} onChange={() => void load()} onError={setError} />
              </div>
            </Panel>
          </div>
        )}
      </div>
    </AppShell>
  );
}
