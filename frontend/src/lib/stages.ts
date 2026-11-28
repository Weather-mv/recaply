import type { PipelineStage, StageKey } from "./types";

export const STAGE_ORDER: StageKey[] = ["download", "analyze", "transcribe", "score", "render"];

export const STAGE_LABEL: Record<StageKey, string> = {
  download: "Downloading",
  analyze: "Analyzing",
  transcribe: "Transcribing",
  score: "Scoring",
  render: "Rendering",
};

/** The stepper's steps, whatever the server sent (older jobs have none: every step is pending). */
export function normalizeStages(stages: PipelineStage[] | undefined | null): PipelineStage[] {
  const by = new Map((stages ?? []).map((s) => [s.key, s]));
  return STAGE_ORDER.map(
    (key) => by.get(key) ?? { key, label: STAGE_LABEL[key], state: "pending", progress: 0, started: null, finished: null, error: null, detail: "" },
  );
}

/** The steps shown while a project is being analysed (Rendering only appears once an export starts). */
export function visibleStages(stages: PipelineStage[], withRender = false): PipelineStage[] {
  const all = normalizeStages(stages);
  return withRender || all.find((s) => s.key === "render")?.state !== "pending" ? all : all.filter((s) => s.key !== "render");
}

/** Overall 0..1 progress across the visible steps (done = 1, running = its own progress, skipped = 1). */
export function overallProgress(stages: PipelineStage[]): number {
  if (!stages.length) return 0;
  const sum = stages.reduce((a, s) => a + (s.state === "done" || s.state === "skipped" ? 1 : s.state === "running" ? s.progress : 0), 0);
  return sum / stages.length;
}

export function seconds(s: PipelineStage): number | null {
  return s.started && s.finished ? Math.max(0, s.finished - s.started) : null;
}

export const SCORE_AXES = [
  { key: "hook_score", label: "Hook", hint: "Does it grab attention in the first 3 seconds?" },
  { key: "engagement_score", label: "Engagement", hint: "Does it keep people watching?" },
  { key: "value_score", label: "Value", hint: "Funny, insightful or shocking?" },
  { key: "shareability_score", label: "Share", hint: "Would someone send it to a friend?" },
] as const;
