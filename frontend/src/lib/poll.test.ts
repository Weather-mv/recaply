import { afterEach, describe, expect, it, vi } from "vitest";
import { pollList } from "@/hooks/data";

type Row = { id: string; state: string; n: number };
const busy = (r: Row) => r.state === "running";

function serve(rows: Row[]) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    const ids = new URL(url, "http://x").searchParams.get("ids");
    const out = ids === null ? rows : rows.filter((r) => ids.split(",").includes(r.id));
    return new Response(JSON.stringify({ items: out }), { status: 200 });
  });
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("pollList", () => {
  it("fetches only the busy rows between full reloads", async () => {
    const calls = serve([{ id: "a", state: "running", n: 2 }, { id: "b", state: "ready", n: 9 }]);
    const prev: Row[] = [{ id: "a", state: "running", n: 1 }, { id: "b", state: "ready", n: 1 }];
    const r = await pollList<Row>("/api/editor/library", "items", prev, busy, Date.now());
    expect(r.full).toBe(false);
    expect(calls).toEqual(["/api/editor/library?ids=a"]);
    expect(r.rows.map((x) => x.n)).toEqual([2, 1]);          // a refreshed, b kept as it was
  });

  it("reloads everything when nothing is busy, at the start, or after 15 s", async () => {
    const calls = serve([{ id: "a", state: "ready", n: 3 }]);
    expect((await pollList<Row>("/l", "items", null, busy, 0)).full).toBe(true);
    expect((await pollList<Row>("/l", "items", [{ id: "a", state: "ready", n: 1 }], busy, Date.now())).full).toBe(true);
    expect((await pollList<Row>("/l", "items", [{ id: "a", state: "running", n: 1 }], busy, Date.now() - 20000)).full).toBe(true);
    expect(calls).toEqual(["/l", "/l", "/l"]);
  });
});
