const BASE = ""; // vite dev proxy handles /api

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

export interface AssetDTO {
  id: number;
  path: string;
  media_type: string;
  size_bytes: number | null;
  mtime: number | null;
  taken_at: number | null;
  width: number | null;
  height: number | null;
  duration_sec: number | null;
  updated_at: number | null;
  basic_info?: Record<string, unknown> | null;
}

export interface PluginResultDTO {
  status: string;
  version: string;
  elapsed_ms: number | null;
  finished_at: number | null;
  data: Record<string, unknown>;
}

export interface AssetDetailDTO extends AssetDTO {
  plugin_results: Record<string, PluginResultDTO>;
}

export interface AssetPage {
  items: AssetDTO[];
  next_cursor: number | null;
}

export interface Facets {
  tags: Record<string, number>;
  categories: Record<string, number>;
  persons: Record<string, number>;
}

export interface JobStats {
  total: number;
  pending: number;
  running: number;
  done: number;
  failed: number;
  progress: number;
  total_est: number;
  done_est: number;
}

export interface JobItem {
  id: number;
  plugin_id: string;
  state: string;
  attempts: number;
  est_cost: number | null;
  actual_cost: number | null;
  error: string | null;
  enqueued_at: number | null;
  started_at: number | null;
  finished_at: number | null;
}

export interface FileJobItem {
  asset_id: number;
  filename: string;
  media_type: string | null;
  state: "active" | "done" | "failed";
  enqueued_at: number | null;
  jobs: JobItem[];
}

export interface JobsResponse {
  stats: JobStats;
  items: FileJobItem[];
}

export interface FolderRoot {
  media_root: string;
  total: number;
  media_types: { image: number; video: number; other: number };
}

export interface FolderResponse {
  roots: FolderRoot[];
}

export const api = {
  health: () => request<{ status: string; version: string }>("/health"),
  assets: (cursor?: number, mediaType?: string, facets?: Record<string, string>) =>
    request<AssetPage>(
      `/assets?${new URLSearchParams({
        limit: "100",
        ...(cursor ? { cursor: String(cursor) } : {}),
        ...(mediaType ? { media_type: mediaType } : {}),
        ...(facets?.tag ? { tag: facets.tag } : {}),
        ...(facets?.category ? { category: facets.category } : {}),
        ...(facets?.person ? { person: facets.person } : {}),
      }).toString()}`,
    ),
  asset: (id: number) => request<AssetDetailDTO>(`/assets/${id}`),
  facets: () => request<Facets>("/facets"),
  jobs: () => request<JobsResponse>("/jobs"),
  retryJob: (id: number) =>
    request<{ ok: boolean }>(`/jobs/${id}/retry`, { method: "POST" }),
  scan: () =>
    request<{ new: number; skipped: number; enqueued: number }>("/scan", {
      method: "POST",
    }),
  folders: () => request<FolderResponse>("/folders"),
};

export function mediaLabel(t: string): string {
  switch (t) {
    case "image":
      return "图片";
    case "video":
      return "视频";
    default:
      return "其他";
  }
}
