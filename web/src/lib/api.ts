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

export interface PersonDTO {
  id: number;
  name: string;
  info: Record<string, unknown>;
  face_count: number;
  asset_ids: number[];
  created_at: number;
  updated_at: number;
}

export interface PluginDTO {
  id: string;
  name: string;
  version: string;
  supported_media: string[];
  depends_on: string[];
  enabled: boolean;
  params: Record<string, unknown>;
  params_schema: Record<string, unknown>;
}

export interface SearchHitDTO {
  asset_id: number;
  media_type: string;
  duration_sec: number | null;
  score: number;
  rank: number;
  scope: string;
  t_start: number | null;
  t_end: number | null;
  can_seek: boolean;
}

export interface SearchResponse {
  query: string;
  total: number;
  items: SearchHitDTO[];
}

export interface AlbumDTO {
  id: number;
  name: string;
  description: string;
  cover_asset_id: number | null;
  asset_count: number;
  asset_ids: number[];
  created_at: number;
  updated_at: number;
}

export interface PlaceCluster {
  grid: [number, number];
  lat: number;
  lon: number;
  count: number;
  asset_ids: number[];
}

export interface PlacesResponse {
  items: PlaceCluster[];
  grid: number;
}

export interface UploadPresetDTO {
  id: number;
  name: string;
  is_builtin: boolean;
  plugin_ids: string[];
  created_at: number;
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
        ...(facets?.person_id ? { person_id: facets.person_id } : {}),
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
  persons: () => request<{ items: PersonDTO[] }>("/persons?include_assets=true"),
  person: (id: number) => request<PersonDTO>(`/persons/${id}`),
  updatePerson: (id: number, body: Record<string, unknown>) =>
    request<PersonDTO>(`/persons/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  mergePersons: (keepId: number, removeId: number) =>
    request<{ ok: boolean; moved: number }>("/persons/merge", {
      method: "POST",
      body: JSON.stringify({ keep_id: keepId, remove_id: removeId }),
    }),
  plugins: () => request<{ items: PluginDTO[] }>("/plugins"),
  setPluginEnabled: (id: string, enabled: boolean) =>
    request<PluginDTO>(`/plugins/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  setPluginParams: (id: string, enabled: boolean, params: Record<string, unknown>) =>
    request<PluginDTO>(`/plugins/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled, params }),
    }),
  rerunPlugin: (id: string) =>
    request<{ ok: boolean; dropped: number; enqueued: number }>(
      `/plugins/${encodeURIComponent(id)}/rerun`,
      { method: "POST" },
    ),
  search: (q: string, limit = 40) =>
    request<SearchResponse>(`/search?${new URLSearchParams({ q, limit: String(limit) })}`),
  albums: () => request<{ items: AlbumDTO[] }>("/albums"),
  createAlbum: (name: string, description = "", assetIds: number[] = []) =>
    request<AlbumDTO>("/albums", {
      method: "POST",
      body: JSON.stringify({ name, description, asset_ids: assetIds }),
    }),
  album: (id: number) => request<AlbumDTO>(`/albums/${id}`),
  updateAlbum: (id: number, body: Record<string, unknown>) =>
    request<AlbumDTO>(`/albums/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  addToAlbum: (id: number, assetIds: number[]) =>
    request<{ ok: boolean; added: number; album: AlbumDTO }>(`/albums/${id}/assets`, {
      method: "POST",
      body: JSON.stringify({ asset_ids: assetIds }),
    }),
  removeFromAlbum: (id: number, assetIds: number[]) =>
    request<{ ok: boolean; removed: number; album: AlbumDTO }>(`/albums/${id}/assets`, {
      method: "DELETE",
      body: JSON.stringify({ asset_ids: assetIds }),
    }),
  deleteAlbum: (id: number) =>
    request<{ ok: boolean }>(`/albums/${id}`, { method: "DELETE" }),
  places: () => request<PlacesResponse>("/places"),
  uploadPresets: () => request<{ items: UploadPresetDTO[] }>("/upload-presets"),
  createUploadPreset: (name: string, pluginIds: string[]) =>
    request<UploadPresetDTO>("/upload-presets", {
      method: "POST",
      body: JSON.stringify({ name, plugin_ids: pluginIds }),
    }),
  deleteUploadPreset: (id: number) =>
    request<{ ok: boolean }>(`/upload-presets/${id}`, { method: "DELETE" }),
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

export function thumbUrl(assetId: number, size: "small" | "medium" | "placeholder" = "small"): string {
  return `/api/assets/${assetId}/thumbnail?size=${size}`;
}
