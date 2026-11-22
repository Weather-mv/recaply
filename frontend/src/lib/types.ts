/** Shapes of what the Flask API returns (see clip_routes.view, studio_app and studio_routes). */

export type BeatType = "hook" | "setup" | "build" | "payoff" | "reaction" | "tangent";

/** One story beat of a clip, in absolute source seconds (the Ollama JSON after validation). */
export interface Beat {
  start: number;
  end: number;
  type: BeatType;
  score: number;
  reason: string;
  time?: string;
}

/** The four 0-10 axes of the AI's virality score and the 0-100 overall (null for jobs made before they existed). */
export interface Virality {
  hook_score: number;
  engagement_score: number;
  value_score: number;
  shareability_score: number;
  overall_virality: number;
}

export interface Clip {
  id: number;
  rank?: number;
  start: number;
  end: number;
  duration: number;
  time: string;
  score: number;
  llm_score: number | null;
  virality?: Virality | null;
  signal_score: number;
  text_score: number;
  reason: string;
  title: string;
  hook: string;
  description: string;
  hashtags: string[];
  emphasis: string[];
  beats: Beat[];
  beat_score: number | null;
  transcript: string;
  signals: Record<string, number>;
  window: [number, number];
}

export type StageKey = "download" | "analyze" | "transcribe" | "score" | "render";
export type StageState = "pending" | "running" | "done" | "failed" | "skipped";

/** One step of the pipeline stepper (recaply/jobs/stages.py). */
export interface PipelineStage {
  key: StageKey;
  label: string;
  state: StageState;
  progress: number; // 0..1 inside the stage
  started: number | null;
  finished: number | null;
  error: string | null;
  detail: string;
}

export interface LogEntry {
  t: number;
  level: "info" | "warning" | "error";
  stage: StageKey | null;
  msg: string;
}

export interface JobLog {
  id: string;
  state: JobState;
  stage: string;
  error: string | null;
  stages: PipelineStage[];
  current: StageKey | null;
  failed_stage: StageKey | null;
  log: LogEntry[];
  notes: string[];
}

export type JobState = "queued" | "running" | "ready" | "failed" | "cancelled";

export interface CutInfo {
  state: string; // queued | cutting | rendering | done | failed
  file?: string;
  error?: string | null;
  stage?: string;
  size_mb?: number;
  layout?: string;
  ass?: string; // the karaoke captions as a subtitle file
  teaser?: boolean;
  music?: string | null;
  t?: number; // when a preview was made (to bust the browser cache of the same file name)
  duration?: number;
  start?: number;
  end?: number;
  notes?: string[];
}

export interface JobResult {
  source: { kind: "url" | "file"; key: string; title: string; duration: number; extractor?: string; video_id?: string };
  profile: { key: string; label: string; reason: string; clip_min: number; clip_max: number };
  scorer: string;
  clips: Clip[];
  shortlist: number;
  signals_used: string[];
  summary?: Summary;
}

export interface Summary {
  total_sec?: number;
  est_usd?: number;
  stages?: Record<string, { sec: number; runs: number; cached: boolean }>;
  notes?: string[];
}

export interface Job {
  id: string;
  state: JobState;
  task: string | null;
  stage: string;
  progress: number;
  title: string;
  error: string | null;
  created: number;
  result: JobResult | null;
  cuts: Record<string, CutInfo>;
  summary: Summary;
  options: Record<string, unknown>;
  version: number;
  media: { source: boolean; audio: boolean };
  stages?: PipelineStage[];
  current?: StageKey | null;
  failed_stage?: StageKey | null;
}

export interface Project {
  id: string;
  title: string;
  state: JobState;
  stage: string;
  progress: number;
  created: number;
  error: string | null;
  profile: { key: string | null; label: string | null; reason: string | null } | null;
  clips: number;
  best: number | null;
  beats: number;
  duration: number | null;
  thumb: string | null;
  extractor: string | null;
  scorer?: string | null;
  stages?: PipelineStage[];
  current?: StageKey | null;
  failed_stage?: StageKey | null;
}

export interface Word {
  i: number;
  w: string;
  s: number; // seconds from the clip's start
  e: number;
}

export interface WordsReply {
  words: Word[];
  fillers: number[];
  duration: number;
  cut: string | null;
  edit: CutInfo | null;
  short: CutInfo | null;
}

export interface Choice {
  key: string;
  label: string;
}

export interface Capabilities {
  exports: Choice[];
  layouts: Choice[];
  captions: Choice[];
  languages: Choice[];
  music: { name: string; size_mb: number }[];
}

export interface Session {
  csrf: string;
  desktop: boolean;
  user: { name: string; email: string | null };
  plan: { name?: string; left?: number | null; limit?: number | null; can_create: boolean; reason?: string | null };
  llm: string;
  profiles: { key: string; label: string; kind: string; hidden: boolean }[];
}

export interface Health {
  llm: string;
  llm_order?: string[];
  ollama: { running: boolean; model: string; model_ready: boolean; hint: string };
  queue: { backend: string; waiting: number; workers: number; ready: boolean };
}

export interface ShortOptions {
  export: string;
  layout: string;
  captions: string;
  hook: boolean;
  emoji: boolean;
  tighten: boolean;
  broll: boolean;
  translate: string;
  teaser: boolean;
  bgm: string; // "" none | "auto" | a file name in assets/bgm
  bgm_level: number;
}

export interface SettingRow {
  name: string;
  label: string;
  secret: boolean;
  set: boolean;
  source: "ui" | "env" | "none";
  value: string;
}

export interface SettingsReply {
  settings: SettingRow[];
  problems: Record<string, string>;
  ai: { provider: string; order: string[]; ready: boolean; ollama: Health["ollama"] };
  providers: string[];
}
