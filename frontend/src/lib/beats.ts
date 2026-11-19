import type { Beat, BeatType } from "./types";

/** How each beat type is named and coloured. "build" is shown as Tension (what the UI calls rising stakes). */
export const BEAT_META: Record<BeatType, { label: string; blurb: string; bar: string; text: string; soft: string }> = {
  hook: { label: "Hook", blurb: "Grabs attention", bar: "bg-beat-hook", text: "text-beat-hook", soft: "bg-beat-hook/15" },
  setup: { label: "Setup", blurb: "Context", bar: "bg-beat-setup", text: "text-beat-setup", soft: "bg-beat-setup/15" },
  build: { label: "Tension", blurb: "Rising stakes", bar: "bg-beat-build", text: "text-beat-build", soft: "bg-beat-build/15" },
  payoff: { label: "Payoff", blurb: "The moment", bar: "bg-beat-payoff", text: "text-beat-payoff", soft: "bg-beat-payoff/15" },
  reaction: { label: "Reaction", blurb: "Response", bar: "bg-beat-reaction", text: "text-beat-reaction", soft: "bg-beat-reaction/15" },
  tangent: { label: "Tangent", blurb: "Off-topic", bar: "bg-beat-tangent", text: "text-beat-tangent", soft: "bg-beat-tangent/15" },
};

export const BEAT_ORDER: BeatType[] = ["hook", "setup", "build", "payoff", "reaction", "tangent"];

export function groupBeats(beats: Beat[]): Record<BeatType, Beat[]> {
  const out = Object.fromEntries(BEAT_ORDER.map((t) => [t, [] as Beat[]])) as Record<BeatType, Beat[]>;
  for (const b of beats) (out[b.type] ?? out.build).push(b);
  return out;
}

/** The beat playing at absolute source time t (the last one that started), or null. */
export function beatAt(beats: Beat[], t: number): Beat | null {
  let hit: Beat | null = null;
  for (const b of beats) if (t >= b.start && t < b.end) hit = b;
  return hit;
}
