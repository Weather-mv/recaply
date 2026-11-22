import { describe, expect, it } from "vitest";
import { normalizeStages, overallProgress, seconds, visibleStages } from "./stages";
import type { PipelineStage } from "./types";

const st = (key: PipelineStage["key"], state: PipelineStage["state"], progress = 0, started: number | null = null, finished: number | null = null): PipelineStage => ({
  key, label: key, state, progress, started, finished, error: null, detail: "",
});

describe("pipeline stages", () => {
  it("fills in every step for jobs made before stages existed", () => {
    const all = normalizeStages(undefined);
    expect(all.map((s) => s.key)).toEqual(["download", "analyze", "transcribe", "score", "render"]);
    expect(all.every((s) => s.state === "pending")).toBe(true);
  });

  it("hides Rendering until an export starts", () => {
    expect(visibleStages(normalizeStages([])).map((s) => s.key)).toEqual(["download", "analyze", "transcribe", "score"]);
    expect(visibleStages(normalizeStages([st("render", "running")])).map((s) => s.key)).toContain("render");
  });

  it("adds up progress: done and skipped count fully, the running step by its own bar", () => {
    const steps = visibleStages(normalizeStages([st("download", "done"), st("analyze", "skipped"), st("transcribe", "running", 0.5)]));
    expect(overallProgress(steps)).toBeCloseTo((1 + 1 + 0.5) / 4);
    expect(overallProgress([])).toBe(0);
  });

  it("times a finished step", () => {
    expect(seconds(st("download", "done", 1, 100, 112.4))).toBeCloseTo(12.4);
    expect(seconds(st("download", "running", 0.2, 100, null))).toBeNull();
  });
});
