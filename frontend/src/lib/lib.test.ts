import { describe, expect, it } from "vitest";
import { beatAt, groupBeats } from "./beats";
import { containRect, coverCrop, faceX, planFrame, resolveLayout, type LayoutInfo } from "./reframe";
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

  // what the server measured on a real GTA stream: the facecam sits in the top-right corner
  const info: LayoutInfo = {
    cam: [0.8281, 0.0933, 0.1719, 0.2313], cam_face: [0.8672, 0.1639, 0.0447, 0.1023], game: [0.0052, 0.056, 0.9896, 0.8899],
    face_keys: [[0, 0.6], [2, 0.6], [2.6, 0.3]], face_seen: 1, action_cx: 0.48, auto: "split", w: 1280, h: 714,
  };
  const opts = { ratio: 0.42, camBox: null, time: 0 };

  it("plans one draw for crops, a blurred backdrop plus the frame for fit", () => {
    expect(planFrame("center", 1920, 1080, 540, 960)).toHaveLength(1);
    const fit = planFrame("fit", 1920, 1080, 540, 960);
    expect(fit.map((d) => !!d.blur)).toEqual([true, false]);
  });

  it("puts the measured facecam on top and the gameplay below, without stretching", () => {
    const split = planFrame("split", 1280, 714, 540, 960, info, opts);
    const [, game, cam] = split;
    expect(cam.dst.y).toBe(0);
    expect(cam.dst.h).toBe(Math.round(960 * 0.42));
    expect(game.dst.y).toBe(cam.dst.h);
    expect(cam.dst.h + game.dst.h).toBe(960);
    expect(cam.src.x).toBeGreaterThanOrEqual(0.8281 * 1280 - 1); // inside the detected cam box
    expect(cam.src.y + cam.src.h).toBeLessThanOrEqual((0.0933 + 0.2313) * 714 + 1);
    for (const d of [game, cam]) expect(d.src.w / d.src.h).toBeCloseTo(d.dst.w / d.dst.h, 3);
  });

  it("follows auto, cam below, the cam box, a drawn box and the facecam size", () => {
    expect(resolveLayout("auto", info, null)).toBe("split");
    expect(resolveLayout("auto", { ...info, cam: null, auto: "action" }, null)).toBe("action");
    expect(resolveLayout("auto", { ...info, cam: null, auto: "action" }, [0, 0, 0.3, 0.3])).toBe("split");
    const below = planFrame("split_bottom", 1280, 714, 540, 960, info, { ...opts, ratio: 0.3 });
    expect(below[2].dst.y).toBe(960 - Math.round(960 * 0.3)); // the cam pane at the bottom, 30% tall
    expect(below[1].dst.y).toBe(0);
    const pip = planFrame("pip", 1280, 714, 540, 960, info, opts);
    expect(pip[1].dst).toEqual({ x: 0, y: 0, w: 540, h: 960 });
    expect(pip[2].frame).toBe(true);
    expect(pip[2].dst.y).toBeLessThan(960 * 0.2);
    const drawn = planFrame("split", 1280, 714, 540, 960, info, { ...opts, camBox: [0, 0.5, 0.25, 0.5] });
    expect(drawn[2].src.x).toBeLessThan(0.25 * 1280); // the drawn box wins over the detected one
  });

  it("pans the face crop along the measured path", () => {
    expect(faceX(info.face_keys, 0)).toBeCloseTo(0.6);
    expect(faceX(info.face_keys, 2.3)).toBeGreaterThan(0.3);
    expect(faceX(info.face_keys, 9)).toBeCloseTo(0.3);
    const a = planFrame("face", 1280, 714, 540, 960, info, { ...opts, time: 0 })[0].src.x;
    const b = planFrame("face", 1280, 714, 540, 960, info, { ...opts, time: 9 })[0].src.x;
    expect(b).toBeLessThan(a);
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

import { captionBox, captionState, chunkWords, resolveStyle } from "./captions";

describe("caption styles", () => {
  const words = "one two three. four five six seven".split(" ").map((w, i) => ({ i, w, s: i * 0.4, e: i * 0.4 + 0.3 }));

  it("puts the panel's changes over the preset", () => {
    const st = resolveStyle({ per: 3, size: 84, pop: true, color: "#ffffff" }, { color: "#ff0000", enter: "fade", motion: "zoom" });
    expect(st.color).toBe("#ff0000");
    expect(st.enter).toBe("fade");
    expect(st.pop).toBe(false);
    expect(st.motion).toBe("zoom");
    expect(resolveStyle({ pop: false }, {}).enter).toBe("none");
  });

  it("chunks like the server: per-word limit and sentence ends", () => {
    const c = chunkWords(words, 3);
    expect(c.map((x) => x.words.map((w) => w.w).join(" "))).toEqual(["one two three.", "four five six", "seven"]);
    expect(chunkWords(words, 1)).toHaveLength(7);
  });

  it("fades and pops lines in, and finds the spoken word", () => {
    const chunks = chunkWords(words, 3);
    const fade = captionState(chunks, 0.1, resolveStyle({}, { enter: "fade" }))!;
    expect(fade.sc).toBe(1);
    expect(fade.op).toBeCloseTo(0.5, 1);
    const pop = captionState(chunks, 0.05, resolveStyle({}, { enter: "pop" }))!;
    expect(pop.sc).toBeLessThan(1);
    expect(captionState(chunks, 0.85, resolveStyle({}, {}))!.j).toBe(2);
    expect(captionState(chunks, 99, resolveStyle({}, {}))).toBeNull();
  });
});

describe("captionBox", () => {
  it("matches the export's box and keeps it on screen", () => {
    const d = captionBox({ size: 84 }, 1080, 1920, 960);
    expect(d.x).toBeCloseTo(1080 * 0.08);
    expect(d.w).toBeCloseTo(1080 * 0.84);
    const l = captionBox({ size: 84, x: 0.3, w: 0.5 }, 1080, 1920, 960);
    expect(l.x + l.w / 2).toBeCloseTo(324);
    expect(l.w).toBeCloseTo(540);
    const n = captionBox({ size: 84, x: 0.2, w: 0.9 }, 1080, 1920, 960);
    expect(n.x).toBeGreaterThanOrEqual(0);
    expect(n.x + n.w / 2).toBeCloseTo(216);
  });
});
