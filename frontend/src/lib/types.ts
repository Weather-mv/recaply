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

export interface Clip {
  id: number;
  rank?: number;
  start: number;
  end: number;
  duration: number;
  time: string;
  score: number;
  llm_score: number | null;
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

export type JobState = "queued" | "running" | "ready" | "failed" | "cancelled";

export interface CutInfo {
  state: string; // queued | cutting | rendering | done | failed
  file?: string;
  error?: string | null;
  stage?: string;
  size_mb?: number;
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
}
