import { describe, expect, it } from "vitest";
import { beatAt, groupBeats } from "./beats";
import { containRect, coverCrop, planFrame } from "./reframe";
import { describeActivity } from "./status";
import type { Beat, Project, Word } from "./types";
import { fmtPrecise, fmtTime } from "./utils";
import { activeWordIndex, cutRanges, keptRanges, paragraphs } from "./words";

const words = (spec: [string, number, number][]): Word[] => spec.map(([w, s, e], i) => ({ i, w, s, e }));

describe("reframe", () => {
  it("crops a 16:9 source to a centred 9:16 column", () => {
    const r = coverCrop(1920, 1080, 540, 960);
    expect(r.h).toBe(1080);
    expect(r.w).toBeCloseTo(607.5);
    expect(r.x).toBeCloseTo((1920 - 607.5) / 2);
  });

  it("pans and handles tall sources", () => {
    expect(coverCrop(1920, 1080, 540, 960, 0).x).toBe(0);
    const tall = coverCrop(1080, 2400, 540, 960); // 9:20 source into 9:16: crops top and bottom
    expect(tall.w).toBe(1080);
    expect(tall.h).toBeCloseTo(1920);
    expect(tall.y).toBeCloseTo(240);
  });

  it("fits the whole frame, centred", () => {
    const r = containRect(1920, 1080, 540, 960);
    expect(r.w).toBe(540);
    expect(r.h).toBeCloseTo(303.75);
    expect(r.y).toBeCloseTo((960 - 303.75) / 2);
  });

  it("plans one draw for crops, a blurred backdrop plus the frame for fit, two panes for split", () => {
    expect(planFrame("center", 1920, 1080, 540, 960)).toHaveLength(1);
    const fit = planFrame("fit", 1920, 1080, 540, 960);
    expect(fit.map((d) => !!d.blur)).toEqual([true, false]);
    const split = planFrame("split", 1920, 1080, 540, 960);
    expect(split).toHaveLength(2);
    expect(split[0].dst.h + split[1].dst.h).toBe(960);
    expect(split[0].src.x).toBeGreaterThanOrEqual(960); // the camera comes from the right half
    for (const d of split) {
      expect(d.src.w / d.src.h).toBeCloseTo(d.dst.w / d.dst.h, 3); // no stretching
    }
  });
});

describe("words", () => {
  const w = words([["No", 1, 1.3], ["way.", 1.4, 1.8], ["Wow", 4, 4.3]]);

  it("finds the spoken word and goes quiet in pauses", () => {
    expect(activeWordIndex(w, 0.5)).toBe(-1);
    expect(activeWordIndex(w, 1.5)).toBe(1);
    expect(activeWordIndex(w, 2.2)).toBe(1); // within half a second of the end
    expect(activeWordIndex(w, 3)).toBe(-1);
    expect(activeWordIndex(w, 4.1)).toBe(2);
    expect(activeWordIndex([], 1)).toBe(-1);
  });

  it("splits paragraphs at long pauses and after sentence ends", () => {
    expect(paragraphs(w, 99).map((p) => p.length)).toEqual([2, 1]);
    const long = words(Array.from({ length: 30 }, (_, i) => [i === 14 ? "end." : "a", i * 0.4, i * 0.4 + 0.3] as [string, number, number]));
    expect(paragraphs(long, 14).map((p) => p.length)).toEqual([15, 15]);
  });

  it("works out what stays after deleting words", () => {
    const x = words([["a", 0, 1], ["um", 1, 1.5], ["b", 1.6, 2], ["c", 5, 6]]);
    expect(keptRanges(x, new Set([1]), 6)).toEqual([[0, 1], [1.5, 6]]);
    expect(keptRanges(x, new Set(), 6)).toEqual([[0, 6]]);
    expect(keptRanges(x, new Set([0, 1, 2]), 6)).toEqual([[2, 6]]);
    expect(cutRanges(x, new Set([1, 2]))).toEqual([[1, 2]]);      // neighbours merge into one cut
    expect(cutRanges(x, new Set([1, 3]))).toEqual([[1, 1.5], [5, 6]]);
  });
});

describe("beats and formatting", () => {
  const beats: Beat[] = [
    { start: 10, end: 14, type: "hook", score: 60, reason: "" },
    { start: 14, end: 20, type: "payoff", score: 90, reason: "" },
  ];
  it("groups and looks up beats", () => {
    expect(groupBeats(beats).payoff).toHaveLength(1);
    expect(groupBeats(beats).tangent).toHaveLength(0);
    expect(beatAt(beats, 15)?.type).toBe("payoff");
    expect(beatAt(beats, 14)?.type).toBe("payoff");
    expect(beatAt(beats, 25)).toBeNull();
  });
  it("formats time", () => {
    expect(fmtTime(83.4)).toBe("1:23");
    expect(fmtTime(3723)).toBe("1:02:03");
    expect(fmtPrecise(83.44)).toBe("1:23.4");
    expect(fmtPrecise(5)).toBe("0:05.0");
  });
});

describe("global status", () => {
  const p = (over: Partial<Project>): Project => ({ id: "x", title: "t", state: "ready", stage: "", progress: 1, created: 0, error: null, profile: null, clips: 0, best: null, beats: 0, duration: null, thumb: null, extractor: null, ...over });

  it("is idle when nothing runs", () => {
    expect(describeActivity([])).toEqual({ kind: "idle", text: "Idle" });
    expect(describeActivity([p({})]).kind).toBe("idle");
  });
  it("says it is detecting the content type until the profile is known", () => {
    const a = describeActivity([p({ id: "a", state: "running", stage: "Checking what kind of video this is" })]);
    expect(a).toMatchObject({ kind: "detecting", id: "a", text: "Detecting content type…" });
    const b = describeActivity([p({ state: "queued", stage: "" }), p({ state: "running", stage: "Scoring" })]);
    expect(b.text).toBe("Detecting content type… +1");
  });
  it("names the detected type once known", () => {
    const a = describeActivity([p({ state: "running", stage: "Transcribing the best moments", profile: { key: "talk", label: "Podcast / interview / talk", reason: null } })]);
    expect(a).toMatchObject({ kind: "working", text: "Podcast / interview / talk: Transcribing the best moments" });
  });
});
