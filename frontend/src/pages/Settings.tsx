import { CheckCircle2, KeyRound, Loader2, ShieldCheck, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import type { SettingRow, SettingsReply } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Which setting belongs to which AI provider (for the "Test" button). */
const PROVIDER_OF: Record<string, string> = {
  GROQ_API_KEY: "groq",
  GEMINI_API_KEY: "gemini",
  ANTHROPIC_API_KEY: "anthropic",
  OPENAI_API_KEY: "openai",
};
const HELP: Record<string, { text: string; href?: string }> = {
  GROQ_API_KEY: { text: "Free tier. Used for fast transcription and for scoring clips.", href: "https://console.groq.com/keys" },
  GEMINI_API_KEY: { text: "Free tier on Google AI Studio.", href: "https://aistudio.google.com/apikey" },
  ANTHROPIC_API_KEY: { text: "Paid. Optional." },
  OPENAI_API_KEY: { text: "Paid. Optional." },
  RECAPLY_PEXELS_KEY: { text: "Free. Adds stock B-roll to your shorts.", href: "https://www.pexels.com/api/" },
  RECAPLY_LLM_ORDER: { text: "Comma separated, first is tried first. Leave empty for the default: groq, gemini, anthropic, openai, ollama." },
};

function Row({ row, onSaved }: { row: SettingRow; onSaved: (r: SettingsReply) => void }) {
  const [value, setValue] = useState(row.secret ? "" : row.value);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const provider = PROVIDER_OF[row.name];
  const help = HELP[row.name];

  const save = async (v: string) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<SettingsReply>("/api/studio/settings", { json: { set: { [row.name]: v } } });
      if (r.problems[row.name]) setMsg({ ok: false, text: r.problems[row.name] });
      else {
        setMsg({ ok: true, text: v ? "Saved." : "Removed." });
        if (row.secret) setValue("");
      }
      onSaved(r);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Couldn't save." });
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ ok: boolean; message: string }>("/api/studio/settings/test", { json: { provider } });
      setMsg({ ok: r.ok, text: r.message });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "The test failed." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={row.name} className="mb-0 text-base font-semibold text-foreground">{row.label}</Label>
        {row.set ? <Badge variant="success"><CheckCircle2 className="size-3.5" /> {row.source === "env" ? "Set in .env" : "Saved"}</Badge> : <Badge variant="outline">Not set</Badge>}
      </div>
      {help && (
        <p className="text-sm text-muted-foreground">
          {help.text} {help.href && <a href={help.href} target="_blank" rel="noreferrer" className="font-semibold text-primary underline-offset-2 hover:underline">Get one</a>}
        </p>
      )}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          id={row.name}
          type={row.secret ? "password" : "text"}
          autoComplete="off"
          spellCheck={false}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={row.secret ? (row.set ? "Paste a new key to replace it" : "Paste your key") : "groq, gemini, ollama"}
        />
        <div className="flex gap-2">
          <Button type="button" onClick={() => save(value.trim())} disabled={busy || (row.secret && !value.trim())}>
            {busy ? <Loader2 className="animate-spin" /> : null} Save
          </Button>
          {provider && (
            <Button type="button" variant="secondary" onClick={test} disabled={busy || !row.set}>Test</Button>
          )}
          {row.set && row.source === "ui" && (
            <Button type="button" variant="ghost" onClick={() => save("")} disabled={busy}>Remove</Button>
          )}
        </div>
      </div>
      {msg && (
        <p role="status" className={cn("flex items-start gap-2 text-sm font-medium", msg.ok ? "text-beat-payoff" : "text-destructive")}>
          {msg.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> : <TriangleAlert className="mt-0.5 size-4 shrink-0" />} {msg.text}
        </p>
      )}
    </div>
  );
}

export default function Settings() {
  const [data, setData] = useState<SettingsReply | null>(null);
  const [error, setError] = useState("");
  const [testing, setTesting] = useState(false);
  const [ollamaMsg, setOllamaMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api<SettingsReply>("/api/studio/settings"));
      setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't load settings.");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const testOllama = async () => {
    setTesting(true);
    try {
      const r = await api<{ ok: boolean; message: string }>("/api/studio/settings/test", { json: { provider: "ollama" } });
      setOllamaMsg({ ok: r.ok, text: r.message });
    } catch (e) {
      setOllamaMsg({ ok: false, text: e instanceof ApiError ? e.message : "The test failed." });
    } finally {
      setTesting(false);
    }
  };

  const ai = data?.ai;
  return (
    <AppShell>
      <div className="mx-auto max-w-3xl space-y-6">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">Settings</h1>
          <p className="mt-1 text-muted-foreground">Connect an AI so clips are scored and written up by a model, not just by rules.</p>
        </div>

        {error && <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">{error}</p>}
        {!data && !error && <div className="grid place-items-center py-16"><Loader2 className="size-8 animate-spin text-primary" /></div>}

        {data && ai && (
          <>
            <Card className="space-y-3 p-5">
              <h2 className="text-lg font-bold">AI in use</h2>
              <p className="text-sm">
                {ai.order.length ? (
                  <>Clips are scored by <b>{ai.order[0]}</b>{ai.order.length > 1 ? `, falling back to ${ai.order.slice(1).join(", ")}` : ""}, then by rules if all of them fail.</>
                ) : (
                  <>No AI is ready, so clips are scored by rules (loudness, motion and the transcript). Add a key below, or start Ollama.</>
                )}
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Badge variant={ai.ollama.running && ai.ollama.model_ready ? "success" : "outline"}>
                  Ollama: {ai.ollama.running ? (ai.ollama.model_ready ? `ready (${ai.ollama.model})` : "running, model missing") : "not running"}
                </Badge>
                <Button type="button" variant="secondary" size="sm" onClick={testOllama} disabled={testing}>
                  {testing ? <Loader2 className="animate-spin" /> : null} Test Ollama
                </Button>
              </div>
              {ollamaMsg && <p className={cn("text-sm font-medium", ollamaMsg.ok ? "text-beat-payoff" : "text-destructive")}>{ollamaMsg.text}</p>}
              {!ai.ollama.running && <p className="text-sm text-muted-foreground">{ai.ollama.hint}</p>}
            </Card>

            <Card className="p-5">
              <h2 className="flex items-center gap-2 text-lg font-bold"><KeyRound className="size-5 text-primary" /> API keys</h2>
              <p className="mt-1 flex items-start gap-2 text-sm text-muted-foreground">
                <ShieldCheck className="mt-0.5 size-4 shrink-0" /> Keys are stored on this PC only and are never shown again after you save them.
              </p>
              <div className="divide-y">
                {data.settings.map((row) => <Row key={row.name} row={row} onSaved={setData} />)}
              </div>
            </Card>
          </>
        )}
      </div>
    </AppShell>
  );
}
