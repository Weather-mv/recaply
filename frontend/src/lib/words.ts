import type { Word } from "./types";

/** Index of the word being spoken at clip time t (the last one that started, until half a second after it ends), or -1. */
export function activeWordIndex(words: Word[], t: number): number {
  let lo = 0;
  let hi = words.length - 1;
  let hit = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].s <= t) {
      hit = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  if (hit >= 0 && t > words[hit].e + 0.5) return -1;
  return hit;
}

const END = /[.?!…]["')\]]*$/;

/** Words grouped into readable paragraphs: break after a sentence end once it has `min` words, or at a long pause. */
export function paragraphs(words: Word[], min = 14, pause = 1.2): Word[][] {
  const out: Word[][] = [];
  let cur: Word[] = [];
  words.forEach((w, i) => {
    cur.push(w);
    const next = words[i + 1];
    const gap = next ? next.s - w.e : Infinity;
    if (!next || gap > pause || (cur.length >= min && END.test(w.w))) {
      out.push(cur);
      cur = [];
    }
  });
  return out;
}

/** Time ranges removed by deleting `deleted` words; words closer than 0.35 s merge into one cut. */
export function cutRanges(words: Word[], deleted: Set<number>): [number, number][] {
  const cuts: [number, number][] = [];
  for (const w of words) {
    if (!deleted.has(w.i)) continue;
    const last = cuts[cuts.length - 1];
    if (last && w.s - last[1] < 0.35) last[1] = w.e;
    else cuts.push([w.s, w.e]);
  }
  return cuts;
}

/** Segments of [0, duration] still kept when the words in `deleted` are removed (the timeline shows the rest as cuts). */
export function keptRanges(words: Word[], deleted: Set<number>, duration: number): [number, number][] {
  const kept: [number, number][] = [];
  let at = 0;
  for (const [a, b] of cutRanges(words, deleted)) {
    if (a > at) kept.push([at, a]);
    at = Math.max(at, b);
  }
  if (at < duration) kept.push([at, duration]);
  return kept;
}
