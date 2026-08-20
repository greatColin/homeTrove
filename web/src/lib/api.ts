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

export interface BulkResult {
  ok: boolean;
  requested: number;
  affected: number;
  missing: number[];
}

export interface BulkAddToAlbumResult {
  ok: boolean;
  added: number;
  requested: number;
  missing: number[];
}

export interface TrashPage {
  items: AssetDTO[];
  total: number;
  limit: number;
  offset: number;
}

export interface SimilarHitDTO {
  asset_id: number;
  media_type: string;
  duration_sec: number | null;
  distance: number;
  scope: string;
  t_start: number | null;
  t_end: number | null;
}

export interface KeyframeDTO {
  scene: number;
  index: number;
  t_sec: number;
  url: string;
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
  favorite: boolean;
  deleted_at: number | null;
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

export interface FaceDTO {
  id: number;
  asset_id: number;
  asset_filename: string | null;
  cluster_id: number | null;
  source_plugin_id: string;
  source_model_name: string;
  confidence: number | null;
  box: number[] | null;
  frame_index: number | null;
  frame_t: number | null;
}

export interface ClusterSummaryDTO {
  id: number;
  name: string;
  source_plugin_id: string;
  source_model_name: string;
  face_count: number;
  radius: number;
}

export interface ClusterDTO extends ClusterSummaryDTO {
  person_id: number | null;
  representative_face: FaceDTO | null;
  faces: FaceDTO[];
  created_at: number;
  updated_at: number;
}

export interface PersonDTO {
  id: number;
  name: string;
  info: Record<string, unknown>;
  face_count: number;
  cluster_count: number | null;
  asset_ids: number[];
  clusters: ClusterSummaryDTO[] | null;
  created_at: number;
  updated_at: number;
}

export interface PluginDTO {
  id: string;
  name: string;
  description: string;
  version: string;
  supported_media: string[];
  depends_on: string[];
  enabled: boolean;
  category: "implemented" | "stub";
  status: string;
  status_detail: string;
  loaded_at: number | null;
  error_at: number | null;
  params: Record<string, unknown>;
  params_schema: Record<string, unknown>;
}

export interface PluginStatusDTO {
  id: string;
  status: string;
  detail: string;
  loaded_at: number | null;
  error_at: number | null;
}

export interface PluginLogEntryDTO {
  ts: number;
  level: string;
  message: string;
}

export interface RerunCandidate {
  asset_id: number;
  filename: string;
  path: string;
  media_type: string;
}

export interface RerunCandidatesResponse {
  items: RerunCandidate[];
  total: number;
}

export interface AlbumDTO {
  id: number;
  name: string;
  description: string;
  cover_asset_id: number | null;
  is_smart: boolean;
  asset_count: number;
  asset_ids: number[];
  rule: SmartAlbumRule | null;
  created_at: number;
  updated_at: number;
}

export type SmartAlbumOp =
  | "and"
  | "or"
  | "person"
  | "place"
  | "tag"
  | "category"
  | "time"
  | "media_type"
  | "favorite";

export interface SmartAlbumRule {
  op: SmartAlbumOp;
  children?: SmartAlbumRule[];
  person_id?: number;
  place_id?: string;
  value?: string | boolean;
  after?: number;
  before?: number;
}

export interface SharedAlbumDTO {
  id: number;
  name: string;
  description: string;
  cover_asset_id: number | null;
  allow_original: boolean;
  allow_download: boolean;
  expires_at: number | null;
  created_at: number;
  asset_ids: number[];
}

export interface ShareLinkDTO {
  token: string;
  allow_original: boolean;
  allow_download: boolean;
  expires_at: number | null;
  created_at: number;
  share_url: string;
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

export interface VaultStatusDTO {
  enabled: boolean;
  configured: boolean;
  unlocked: boolean;
  total_assets: number | null;
  encrypted_assets: number | null;
}

export interface SettingsDTO {
  encrypt_new_uploads: boolean;
  vault_enabled: boolean;
  vault_configured: boolean;
  vault_unlocked: boolean;
}

export const vaultStatus = () => request<VaultStatusDTO>("/vault/status");
export const vaultSetup = (password: string, confirm: string) =>
  request<{ ok: boolean; unlocked: boolean }>("/vault/setup", {
    method: "POST",
    body: JSON.stringify({ password, confirm }),
  });
export const vaultUnlock = (password: string) =>
  request<{ ok: boolean; unlocked: boolean }>("/vault/unlock", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
export const vaultLock = () =>
  request<{ ok: boolean; unlocked: boolean }>("/vault/lock", { method: "POST" });

export interface AssetsFilters {
  mediaType?: string;
  favorite?: boolean;
  tag?: string;
  category?: string;
  personId?: number;
  takenAfter?: number;
  takenBefore?: number;
  place?: string;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/health"),
  assets: (cursor?: number, filters: AssetsFilters = {}) => {
    const params = new URLSearchParams({ limit: "100" });
    if (cursor !== undefined) params.set("cursor", String(cursor));
    if (filters.mediaType) params.set("media_type", filters.mediaType);
    if (filters.favorite !== undefined) params.set("favorite", String(filters.favorite));
    if (filters.tag) params.set("tag", filters.tag);
    if (filters.category) params.set("category", filters.category);
    if (filters.personId !== undefined) params.set("person_id", String(filters.personId));
    if (filters.takenAfter !== undefined) params.set("taken_after", String(filters.takenAfter));
    if (filters.takenBefore !== undefined) params.set("taken_before", String(filters.takenBefore));
    if (filters.place) params.set("place", filters.place);
    return request<AssetPage>(`/assets?${params.toString()}`);
  },
  asset: (id: number, includeTrashed = false) =>
    request<AssetDetailDTO>(`/assets/${id}?${new URLSearchParams({ include_trashed: String(includeTrashed) })}`),
  similar: (id: number, limit = 24) =>
    request<{ asset_id: number; items: SimilarHitDTO[] }>(`/assets/${id}/similar?${new URLSearchParams({ limit: String(limit) })}`),
  keyframes: (id: number) =>
    request<{ asset_id: number; items: KeyframeDTO[] }>(`/assets/${id}/keyframes`),
  toggleFavorite: (id: number) =>
    request<{ ok: boolean; id: number; favorite: boolean }>(`/assets/${id}/favorite`, {
      method: "POST",
    }),
  moveToTrash: (id: number) =>
    request<{ ok: boolean; id: number; deleted_at: number | null }>(`/assets/${id}/trash`, {
      method: "POST",
    }),
  restoreFromTrash: (id: number) =>
    request<{ ok: boolean; id: number; deleted_at: number | null }>(`/assets/${id}/restore`, {
      method: "POST",
    }),
  trash: (limit = 60, offset = 0) =>
    request<TrashPage>(`/trash?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`),
  emptyTrash: (olderThanSeconds?: number) =>
    request<{ ok: boolean; dropped: number }>(
      `/trash/empty${olderThanSeconds !== undefined ? `?older_than_seconds=${olderThanSeconds}` : ""}`,
      { method: "POST" },
    ),
  bulkTrash: (assetIds: number[]) =>
    request<BulkResult>("/bulk/assets/trash", {
      method: "POST",
      body: JSON.stringify({ asset_ids: assetIds }),
    }),
  bulkRestore: (assetIds: number[]) =>
    request<BulkResult>("/bulk/assets/restore", {
      method: "POST",
      body: JSON.stringify({ asset_ids: assetIds }),
    }),
  bulkFavorite: (assetIds: number[], on: boolean) =>
    request<BulkResult>(on ? "/bulk/assets/favorite" : "/bulk/assets/unfavorite", {
      method: "POST",
      body: JSON.stringify({ asset_ids: assetIds }),
    }),
  bulkAddToAlbum: (assetIds: number[], albumId: number) =>
    request<BulkAddToAlbumResult>("/bulk/assets/add-to-album", {
      method: "POST",
      body: JSON.stringify({ asset_ids: assetIds, album_id: albumId }),
    }),
  facets: () => request<Facets>("/facets"),
  jobs: () => request<JobsResponse>("/jobs"),
  retryJob: (id: number) =>
    request<{ ok: boolean }>(`/jobs/${id}/retry`, { method: "POST" }),
  scan: () =>
    request<{ new: number; skipped: number; enqueued: number }>("/scan", {
      method: "POST",
    }),
  folders: () => request<FolderResponse>("/folders"),
  persons: (includeClusters = false) =>
    request<{ items: PersonDTO[] }>(
      `/persons?include_assets=true${includeClusters ? "&include_clusters=true" : ""}`,
    ),
  person: (id: number, includeClusters = false) =>
    request<PersonDTO>(
      `/persons/${id}${includeClusters ? "?include_clusters=true" : ""}`,
    ),
  updatePerson: (id: number, body: Record<string, unknown>) =>
    request<PersonDTO>(`/persons/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  mergePersons: (keepId: number, removeId: number) =>
    request<{ ok: boolean; moved: number }>("/persons/merge", {
      method: "POST",
      body: JSON.stringify({ keep_id: keepId, remove_id: removeId }),
    }),
  plugins: () => request<{ items: PluginDTO[] }>("/plugins"),
  enabledPlugins: () => request<{ items: PluginDTO[] }>("/plugins?enabled=true"),
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
  pluginStatus: (id: string) =>
    request<PluginStatusDTO>(`/plugins/${encodeURIComponent(id)}/status`),
  pluginLogs: (id: string, limit = 100) =>
    request<{ items: PluginLogEntryDTO[] }>(
      `/plugins/${encodeURIComponent(id)}/logs?limit=${limit}`,
    ),
  downloadPluginModel: (id: string) =>
    request<{ ok: boolean; plugin_id: string; model_name: string }>(
      `/plugins/${encodeURIComponent(id)}/download-model`,
      { method: "POST" },
    ),
  clusters: (params: {
    person_id?: number;
    unassigned?: boolean;
    source_plugin_id?: string;
  } = {}) => {
    const qs = new URLSearchParams();
    if (params.person_id != null) qs.set("person_id", String(params.person_id));
    if (params.unassigned) qs.set("unassigned", "true");
    if (params.source_plugin_id) qs.set("source_plugin_id", params.source_plugin_id);
    const tail = qs.toString();
    return request<{ items: ClusterDTO[] }>(
      `/clusters${tail ? `?${tail}` : ""}`,
    );
  },
  cluster: (id: number) => request<ClusterDTO>(`/clusters/${id}`),
  updateCluster: (
    id: number,
    body: { name?: string; person_id?: number; clear_person?: boolean },
  ) =>
    request<ClusterDTO>(`/clusters/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteCluster: (id: number) =>
    request<{ ok: boolean; deleted: number }>(`/clusters/${id}`, {
      method: "DELETE",
    }),
  reassignFace: (clusterId: number, faceId: number) =>
    request<ClusterDTO>(`/clusters/${clusterId}/faces`, {
      method: "POST",
      body: JSON.stringify({ face_id: faceId }),
    }),
  mergeClusters: (srcId: number, dstId: number) =>
    request<{ ok: boolean; moved: number; deleted_cluster_id: number }>(
      `/clusters/${srcId}/merge-into/${dstId}`,
      { method: "POST" },
    ),
  deleteFace: (id: number) =>
    request<{ ok: boolean; deleted: number }>(`/faces/${id}`, {
      method: "DELETE",
    }),
  rerunPlugin: (id: string) =>
    request<{ ok: boolean; dropped: number; enqueued: number }>(
      `/plugins/${encodeURIComponent(id)}/rerun`,
      { method: "POST" },
    ),
  rerunCandidates: (id: string, q: string = "", offset = 0, limit = 50) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (q) params.set("q", q);
    return request<RerunCandidatesResponse>(
      `/plugins/${encodeURIComponent(id)}/rerun-candidates?${params.toString()}`,
    );
  },
  rerunSelected: (id: string, assetIds: number[]) =>
    request<{ ok: boolean; dropped: number; enqueued: number }>(
      `/plugins/${encodeURIComponent(id)}/rerun-selected`,
      {
        method: "POST",
        body: JSON.stringify({ asset_ids: assetIds }),
      },
    ),
  search: (q: string, limit = 40) =>
    request<SearchResponse>(`/search?${new URLSearchParams({ q, limit: String(limit) })}`),
  albums: () => request<{ items: AlbumDTO[] }>("/albums"),
  createAlbum: (name: string, description = "", assetIds: number[] = []) =>
    request<AlbumDTO>("/albums", {
      method: "POST",
      body: JSON.stringify({ name, description, asset_ids: assetIds }),
    }),
  createSmartAlbum: (name: string, description: string, rule: SmartAlbumRule) =>
    request<AlbumDTO>("/albums", {
      method: "POST",
      body: JSON.stringify({ name, description, is_smart: true, rule }),
    }),
  album: (id: number) => request<AlbumDTO>(`/albums/${id}`),
  updateAlbum: (id: number, body: Record<string, unknown>) =>
    request<AlbumDTO>(`/albums/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  updateSmartAlbumRule: (id: number, rule: SmartAlbumRule) =>
    request<AlbumDTO>(`/albums/${id}`, { method: "PATCH", body: JSON.stringify({ rule }) }),
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
  createShare: (albumId: number, body: Record<string, unknown>) =>
    request<ShareLinkDTO>(`/albums/${albumId}/shares`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listShares: (albumId: number) =>
    request<{ items: ShareLinkDTO[] }>(`/albums/${albumId}/shares`),
  deleteShare: (albumId: number, token: string) =>
    request<{ ok: boolean }>(`/albums/${albumId}/shares/${token}`, { method: "DELETE" }),
  publicAlbum: (token: string) =>
    request<SharedAlbumDTO>(`/public/albums/${token}`),
  places: () => request<PlacesResponse>("/places"),
  uploadPresets: () => request<{ items: UploadPresetDTO[] }>("/upload-presets"),
  createUploadPreset: (name: string, pluginIds: string[]) =>
    request<UploadPresetDTO>("/upload-presets", {
      method: "POST",
      body: JSON.stringify({ name, plugin_ids: pluginIds }),
    }),
  deleteUploadPreset: (id: number) =>
    request<{ ok: boolean }>(`/upload-presets/${id}`, { method: "DELETE" }),
  vaultStatus: () => request<VaultStatusDTO>("/vault/status"),
  vaultSetup: (password: string, confirm: string) =>
    request<{ ok: boolean; unlocked: boolean }>("/vault/setup", {
      method: "POST",
      body: JSON.stringify({ password, confirm }),
    }),
  vaultUnlock: (password: string) =>
    request<{ ok: boolean; unlocked: boolean }>("/vault/unlock", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  vaultLock: () =>
    request<{ ok: boolean; unlocked: boolean }>("/vault/lock", { method: "POST" }),
  getSettings: () => request<SettingsDTO>("/settings"),
  updateSettings: (body: { encrypt_new_uploads: boolean }) =>
    request<SettingsDTO>("/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
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

export function publicThumbUrl(token: string, assetId: number, size: "small" | "medium" | "placeholder" = "small"): string {
  return `/api/public/thumbnails/${token}/${assetId}/${size}`;
}

export function publicFileUrl(token: string, assetId: number): string {
  return `/api/public/files/${token}/${assetId}`;
}
