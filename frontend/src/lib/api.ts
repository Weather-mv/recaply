/** fetch helpers: same-origin, JSON, and the session's CSRF token on every write (like static/js/recaply.js). */

const csrf = (): string => document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "";

export class ApiError extends Error {
  constructor(message: string, public status: number, public body: Record<string, unknown> = {}) {
    super(message);
  }
  get needsLogin(): boolean {
    return this.status === 401;
  }
  get needsUpgrade(): boolean {
    return this.status === 402;
  }
}

export async function api<T>(path: string, init: { method?: string; json?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const method = init.method ?? (init.json !== undefined ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-CSRF-Token"] = csrf();
  let body: string | undefined;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { method, headers, body, credentials: "same-origin", signal: init.signal });
  let data: Record<string, unknown> = {};
  try {
    data = await res.json();
  } catch {
    /* not JSON (an HTML error page) */
  }
  if (res.status === 401 && typeof data.login === "string") {
    window.location.href = data.login as string; // not signed in: go to Recaply's login page
  }
  if (!res.ok) {
    throw new ApiError(String(data.error ?? `Request failed (${res.status})`), res.status, data);
  }
  return data as T;
}

export const clipFileUrl = (jobId: string, name: string): string => `/clipjob/${jobId}/file/${encodeURIComponent(name)}`;
export const mediaUrl = (jobId: string, kind: "source" | "audio"): string => `/api/clipjob/${jobId}/media/${kind}`;
export const thumbUrl = (videoId: string | null | undefined, extractor?: string | null): string | null =>
  videoId && (extractor ?? "").toLowerCase().includes("youtube") ? `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg` : null;
