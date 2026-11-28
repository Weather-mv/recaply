import type { Project } from "./types";

export const isActive = (p: Pick<Project, "state">): boolean => p.state === "queued" || p.state === "running";

/** What the engine is doing right now, in the words a person would use (shown in the header pill). */
export function describeActivity(projects: Project[]): { kind: "idle" | "detecting" | "working"; text: string; id?: string } {
  const active = projects.filter(isActive);
  if (!active.length) return { kind: "idle", text: "Idle" };
  const first = active[0];
  const detecting = !first.profile?.label && /kind of video|detect|reading the link|checking/i.test(first.stage || "Reading the link");
  const more = active.length > 1 ? ` +${active.length - 1}` : "";
  if (detecting) return { kind: "detecting", text: `Detecting content type…${more}`, id: first.id };
  const what = first.profile?.label ? `${first.profile.label}: ` : "";
  return { kind: "working", text: `${what}${first.stage || "Working"}${more}`, id: first.id };
}
