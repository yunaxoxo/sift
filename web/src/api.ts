import { fallbackBenchmark, fallbackSearch, fallbackStatus } from "./fallback";
import type { Benchmark, DocumentText, Mode, SearchResult, Status } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

// A network failure (not an HTTP error) means there is no server at all.
// From then on every call is answered by the bundled fixture.
let serverGone = false;

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers: { Accept: "application/json" } });
  // Static hosts answer /api/* with HTML: that is "no server here", not an API error.
  if (!(response.headers.get("content-type") ?? "").includes("application/json")) {
    throw new TypeError("not a sift API");
  }
  const body: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError((body as { error?: string }).error ?? `HTTP ${response.status}`, response.status);
  }
  return body as T;
}

async function withFallback<T>(live: () => Promise<T>, fixture: () => Promise<T>): Promise<T> {
  if (serverGone) return fixture();
  try {
    return await live();
  } catch (error) {
    if (error instanceof TypeError) {
      serverGone = true;
      return fixture();
    }
    throw error;
  }
}

export const api = {
  status: () => withFallback(() => getJson<Status>("api/status"), fallbackStatus),

  search: (query: string, mode: Mode, k: number, signal?: AbortSignal) =>
    withFallback(
      () => getJson<SearchResult>(`api/search?${new URLSearchParams({ q: query, mode, k: String(k) })}`, signal),
      () => fallbackSearch(query, mode, k),
    ),

  benchmark: () => withFallback(() => getJson<Benchmark>("api/benchmark"), fallbackBenchmark),

  /** Rejects when the file is unavailable; the reader then shows the matched excerpt alone. */
  document: (path: string, signal?: AbortSignal) =>
    serverGone
      ? Promise.reject(new ApiError("offline", 0))
      : getJson<DocumentText>(`api/document?${new URLSearchParams({ path })}`, signal),
};
