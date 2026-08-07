import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, mediaLabel, thumbUrl, type AlbumDTO, type AssetDTO, type ShareLinkDTO, type SmartAlbumRule } from "../lib/api";
import { ShareModal } from "../components/share_modal";
import { JustifiedGrid, type LayoutItem } from "../components/justified_grid";

function AssetThumb({ assetId, video }: { assetId: number; video?: boolean }) {
  return (
    <div className="relative m-[2px] aspect-[4/3] overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800">
      <img
        src={thumbUrl(assetId, video ? "placeholder" : "small")}
        alt=""
        loading="lazy"
        className="h-full w-full object-cover"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.display = "none";
        }}
      />
    </div>
  );
}

function AlbumGridThumb({ item, onClick }: { item: LayoutItem; onClick: (a: AssetDTO) => void }) {
  const isImage = item.asset.media_type === "image";
  const style: React.CSSProperties = {
    position: "absolute",
    left: item.left,
    top: item.top,
    width: item.width,
    height: item.height,
  };
  return (
    <Link
      to={`/asset/${item.asset.id}`}
      style={style}
      onClick={(e) => {
        e.preventDefault();
        onClick(item.asset);
      }}
      className="group relative overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800"
    >
      <img
        src={thumbUrl(item.asset.id, isImage ? "small" : "placeholder")}
        alt=""
        loading="lazy"
        className="h-full w-full object-cover"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.display = "none";
        }}
      />
      {!isImage && (
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs font-medium text-white/90">
          <span className="rounded bg-black/50 px-2 py-0.5">{mediaLabel(item.asset.media_type)}</span>
        </span>
      )}
    </Link>
  );
}

function CreateAlbumForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [isSmart, setIsSmart] = useState(false);
  const [rule, setRule] = useState<SmartAlbumRule>({ op: "and", children: [] });
  const qc = useQueryClient();

  const createManual = useMutation({
    mutationFn: () => api.createAlbum(name, desc),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["albums"] });
      onDone();
    },
  });

  const createSmart = useMutation({
    mutationFn: () => api.createSmartAlbum(name, desc, rule),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["albums"] });
      onDone();
    },
  });

  const create = isSmart ? createSmart : createManual;
  const canSubmit = name.trim() && (!isSmart || isValidSmartRule(rule));

  return (
    <form
      className="mt-4 flex flex-col gap-3 rounded-lg border border-neutral-200 p-4 dark:border-neutral-700"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSubmit) create.mutate();
      }}
    >
      <h3 className="font-medium">新建相册</h3>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="相册名称"
        className="rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-600 dark:bg-neutral-800"
      />
      <input
        value={desc}
        onChange={(e) => setDesc(e.target.value)}
        placeholder="描述（可选）"
        className="rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-600 dark:bg-neutral-800"
      />

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={isSmart}
          onChange={(e) => setIsSmart(e.target.checked)}
        />
        智能相册（按规则自动更新）
      </label>

      {isSmart && (
        <div className="rounded border border-neutral-200 p-3 dark:border-neutral-700">
          <p className="mb-2 text-sm font-medium">规则</p>
          <RuleEditor rule={rule} onChange={setRule} />
        </div>
      )}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={!canSubmit || create.isPending}
          className="rounded bg-brand-500 px-3 py-1 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
        >
          {create.isPending ? "创建中…" : "创建"}
        </button>
        <button
          type="button"
          onClick={onDone}
          className="rounded border border-neutral-300 px-3 py-1 text-sm dark:border-neutral-600"
        >
          取消
        </button>
      </div>
      {create.isError && (
        <p className="text-sm text-red-500">创建失败：{(create.error as Error).message}</p>
      )}
    </form>
  );
}

function AlbumCard({ album }: { album: AlbumDTO }) {
  const cover = album.cover_asset_id ?? album.asset_ids[0];
  return (
    <Link
      to={`/albums/${album.id}`}
      className="group block overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-sm transition hover:shadow-md dark:border-neutral-700 dark:bg-neutral-900"
    >
      <div className="relative h-36 bg-neutral-100 dark:bg-neutral-800">
        {cover != null ? (
          <AssetThumb assetId={cover} />
        ) : (
          <div className="flex h-full items-center justify-center text-neutral-400">
            空相册
          </div>
        )}
        <span className="absolute right-2 top-2 rounded bg-black/50 px-1.5 py-0.5 text-xs text-white">
          {album.asset_count} 项
        </span>
        {album.is_smart && (
          <span className="absolute left-2 top-2 rounded bg-brand-500 px-1.5 py-0.5 text-xs text-white">
            智能
          </span>
        )}
      </div>
      <div className="p-3">
        <p className="truncate font-medium">{album.name}</p>
        {album.description && (
          <p className="mt-0.5 truncate text-sm text-neutral-500">{album.description}</p>
        )}
        <p className="mt-1 text-xs text-neutral-400">{fmtDate(album.updated_at)}</p>
      </div>
    </Link>
  );
}

function AlbumList() {
  const [creating, setCreating] = useState(false);
  const { data } = useQuery({ queryKey: ["albums"], queryFn: api.albums });
  const albums = data?.items ?? [];
  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">相册</h1>
        <button
          onClick={() => setCreating((v) => !v)}
          className="rounded bg-brand-500 px-3 py-1 text-sm font-medium text-white hover:bg-brand-600"
        >
          {creating ? "取消" : "新建相册"}
        </button>
      </div>
      {creating && <CreateAlbumForm onDone={() => setCreating(false)} />}
      {albums.length === 0 ? (
        <p className="mt-6 text-sm text-neutral-500">
          还没有相册。点击「新建相册」创建，之后可在资源详情或相册内添加照片。
        </p>
      ) : (
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {albums.map((a) => (
            <AlbumCard key={a.id} album={a} />
          ))}
        </div>
      )}
    </div>
  );
}

function AlbumDetail() {
  const { id } = useParams();
  const albumId = Number(id);
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [rename, setRename] = useState("");

  const { data: album, isLoading, isError } = useQuery({
    queryKey: ["album", albumId],
    queryFn: () => api.album(albumId),
    enabled: Number.isFinite(albumId),
  });
  const { data: allAssets } = useQuery({
    queryKey: ["assets", 0],
    queryFn: () => api.assets(undefined),
    enabled: showAdd,
  });
  const [picked, setPicked] = useState<number[]>([]);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["album", albumId] });

  const add = useMutation({
    mutationFn: (ids: number[]) => api.addToAlbum(albumId, ids),
    onSuccess: () => {
      invalidate();
      setPicked([]);
      setShowAdd(false);
    },
  });
  const remove = useMutation({
    mutationFn: (ids: number[]) => api.removeFromAlbum(albumId, ids),
    onSuccess: invalidate,
  });
  const setCover = useMutation({
    mutationFn: (assetId: number) => api.updateAlbum(albumId, { cover_asset_id: assetId }),
    onSuccess: invalidate,
  });
  const renameMut = useMutation({
    mutationFn: () => api.updateAlbum(albumId, { name: rename }),
    onSuccess: invalidate,
  });
  const del = useMutation({
    mutationFn: () => api.deleteAlbum(albumId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["albums"] }),
  });
  const updateRule = useMutation({
    mutationFn: (rule: SmartAlbumRule) => api.updateSmartAlbumRule(albumId, rule),
    onSuccess: invalidate,
  });

  const [showShare, setShowShare] = useState(false);
  const [editingRule, setEditingRule] = useState(false);

  if (!Number.isFinite(albumId)) return <p className="p-6 text-neutral-500">无效相册 ID</p>;
  if (isLoading) return <p className="p-6 text-neutral-400">加载中…</p>;
  if (isError || !album) return <p className="p-6 text-red-500">相册不存在</p>;

  const inAlbum = new Set(album.asset_ids);
  const candidateAssets = (allAssets?.items ?? []).filter((a) => !inAlbum.has(a.id));
  const isRenaming = rename.trim().length > 0 && rename !== album.name;

  return (
    <div className="p-4 md:p-6">
      <Link to="/albums" className="text-sm text-brand-500 hover:underline">
        ← 返回相册
      </Link>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{album.name}</h1>
        {album.is_smart && (
          <span className="rounded bg-brand-500 px-1.5 py-0.5 text-xs text-white">智能</span>
        )}
        <span className="text-sm text-neutral-500">{album.asset_count} 项</span>
        <button
          onClick={() => del.mutate()}
          className="ml-auto rounded border border-red-300 px-2 py-1 text-sm text-red-600 hover:bg-red-50 dark:border-red-800 dark:hover:bg-red-950"
        >
          删除相册
        </button>
      </div>
      {album.description && <p className="mt-1 text-sm text-neutral-500">{album.description}</p>}

      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        {!album.is_smart && (
          <button
            onClick={() => setShowAdd((v) => !v)}
            className="rounded bg-brand-500 px-3 py-1 font-medium text-white hover:bg-brand-600"
          >
            {showAdd ? "取消" : "添加照片"}
          </button>
        )}
        {album.is_smart && (
          <button
            onClick={() => setEditingRule((v) => !v)}
            className="rounded bg-brand-500 px-3 py-1 font-medium text-white hover:bg-brand-600"
          >
            {editingRule ? "取消" : "编辑规则"}
          </button>
        )}
        <button
          onClick={() => setShowShare(true)}
          className="rounded border border-neutral-300 px-3 py-1 hover:bg-neutral-100 dark:border-neutral-600 dark:hover:bg-neutral-800"
        >
          分享
        </button>
        {isRenaming && (
          <button
            onClick={() => renameMut.mutate()}
            className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-600"
          >
            保存新名称
          </button>
        )}
      </div>

      {editingRule && album.rule && (
        <div className="mt-3 rounded-lg border border-neutral-200 p-3 dark:border-neutral-700">
          <RuleEditor
            rule={album.rule}
            onChange={(rule) => {
              if (isValidSmartRule(rule)) {
                updateRule.mutate(rule);
                setEditingRule(false);
              }
            }}
          />
          {updateRule.isError && (
            <p className="mt-2 text-sm text-red-500">保存失败：{(updateRule.error as Error).message}</p>
          )}
        </div>
      )}

      {showAdd && !album.is_smart && (
        <div className="mt-3 rounded-lg border border-neutral-200 p-3 dark:border-neutral-700">
          <div className="flex items-center justify-between">
            <p className="text-sm text-neutral-500">
              选择要加入的资产（已选 {picked.length}）
            </p>
            <button
              onClick={() => add.mutate(picked)}
              disabled={picked.length === 0 || add.isPending}
              className="rounded bg-brand-500 px-3 py-1 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
            >
              {add.isPending ? "添加中…" : "添加所选"}
            </button>
          </div>
          {candidateAssets.length === 0 ? (
            <p className="mt-2 text-sm text-neutral-500">所有资产都已在此相册中。</p>
          ) : (
            <div className="mt-2 grid grid-cols-3 gap-1 sm:grid-cols-4 lg:grid-cols-6">
              {candidateAssets.map((a) => {
                const sel = picked.includes(a.id);
                return (
                  <button
                    key={a.id}
                    onClick={() =>
                      setPicked((p) => (sel ? p.filter((x) => x !== a.id) : [...p, a.id]))
                    }
                    className={`relative overflow-hidden rounded-sm border-2 ${
                      sel
                        ? "border-brand-500"
                        : "border-transparent hover:border-neutral-300 dark:hover:border-neutral-600"
                    }`}
                  >
                    <AssetThumb assetId={a.id} video={a.media_type === "video"} />
                    {sel && (
                      <span className="absolute right-1 top-1 rounded bg-brand-500 px-1 text-xs text-white">
                        ✓
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      <input
        value={rename}
        onChange={(e) => setRename(e.target.value)}
        placeholder="重命名相册（输入后保存）"
        onKeyDown={(e) => {
          if (e.key === "Enter" && isRenaming) renameMut.mutate();
        }}
        className="mt-3 w-full max-w-xs rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-600 dark:bg-neutral-800"
      />

      {album.asset_ids.length === 0 ? (
        <p className="mt-6 text-sm text-neutral-500">空相册。</p>
      ) : (
        <JustifiedGrid
          assets={album.asset_ids.map((id) => ({ id } as AssetDTO))}
          renderItem={(item) => (
            <div
              key={item.asset.id}
              style={{
                position: "absolute",
                left: item.left,
                top: item.top,
                width: item.width,
                height: item.height,
              }}
              className="group relative"
            >
              <AlbumGridThumb item={item} onClick={() => {}} />
              {!album.is_smart && (
                <div className="absolute inset-0 hidden items-center justify-center gap-2 bg-black/40 group-hover:flex">
                  <button
                    onClick={() => setCover.mutate(item.asset.id)}
                    title="设为封面"
                    className="rounded bg-white/90 px-1.5 py-0.5 text-xs text-black"
                  >
                    封面
                  </button>
                  <button
                    onClick={() => remove.mutate([item.asset.id])}
                    title="移出相册"
                    className="rounded bg-red-500/90 px-1.5 py-0.5 text-xs text-white"
                  >
                    移除
                  </button>
                </div>
              )}
            </div>
          )}
          className="mt-3 h-[60vh]"
        />
      )}
      {remove.isError && <p className="mt-2 text-sm text-red-500">操作失败</p>}
      {showShare && <ShareModal albumId={albumId} onClose={() => setShowShare(false)} />}
    </div>
  );
}

function RuleEditor({ rule, onChange }: { rule: SmartAlbumRule; onChange: (r: SmartAlbumRule) => void }) {
  const { data: persons } = useQuery({ queryKey: ["persons"], queryFn: api.persons });
  const { data: places } = useQuery({ queryKey: ["places"], queryFn: api.places });
  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: api.facets });

  if (rule.op === "and" || rule.op === "or") {
    return (
      <div className="space-y-2 rounded border border-neutral-200 p-2 dark:border-neutral-700">
        <div className="flex items-center gap-2">
          <select
            value={rule.op}
            onChange={(e) => onChange({ ...rule, op: e.target.value as "and" | "or" })}
            className="rounded border border-neutral-300 px-1 py-0.5 text-sm dark:border-neutral-600 dark:bg-neutral-800"
          >
            <option value="and">全部满足</option>
            <option value="or">任一满足</option>
          </select>
          <button
            type="button"
            onClick={() =>
              onChange({
                ...rule,
                children: [...(rule.children ?? []), { op: "tag", value: "" }],
              })
            }
            className="text-xs text-brand-500 hover:underline"
          >
            + 添加条件
          </button>
          <button
            type="button"
            onClick={() =>
              onChange({
                ...rule,
                children: [...(rule.children ?? []), { op: "and", children: [] }],
              })
            }
            className="text-xs text-brand-500 hover:underline"
          >
            + 添加条件组
          </button>
        </div>
        {(rule.children ?? []).map((child, i) => (
          <div key={i} className="flex items-start gap-2">
            <div className="flex-1">
              <RuleEditor
                rule={child}
                onChange={(newChild) => {
                  const children = [...(rule.children ?? [])];
                  children[i] = newChild;
                  onChange({ ...rule, children });
                }}
              />
            </div>
            <button
              type="button"
              onClick={() => {
                const children = [...(rule.children ?? [])];
                children.splice(i, 1);
                onChange({ ...rule, children });
              }}
              className="text-xs text-red-500 hover:underline"
            >
              删除
            </button>
          </div>
        ))}
      </div>
    );
  }

  const updateField = (patch: Partial<SmartAlbumRule>) => onChange({ ...rule, ...patch });

  switch (rule.op) {
    case "person":
      return (
        <div className="flex items-center gap-2 text-sm">
          <span>人物</span>
          <select
            value={rule.person_id ?? ""}
            onChange={(e) => updateField({ person_id: e.target.value ? Number(e.target.value) : undefined })}
            className="rounded border border-neutral-300 px-1 py-0.5 dark:border-neutral-600 dark:bg-neutral-800"
          >
            <option value="">选择人物</option>
            {(persons?.items ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      );
    case "place":
      return (
        <div className="flex items-center gap-2 text-sm">
          <span>地点</span>
          <select
            value={rule.place_id ?? ""}
            onChange={(e) => updateField({ place_id: e.target.value || undefined })}
            className="rounded border border-neutral-300 px-1 py-0.5 dark:border-neutral-600 dark:bg-neutral-800"
          >
            <option value="">选择地点</option>
            {(places?.items ?? []).map((p) => (
              <option key={`${p.lat},${p.lon}`} value={`${p.lat},${p.lon}`}>
                {p.lat.toFixed(2)},{p.lon.toFixed(2)} ({p.count})
              </option>
            ))}
          </select>
        </div>
      );
    case "tag":
      return (
        <div className="flex items-center gap-2 text-sm">
          <span>标签</span>
          <select
            value={typeof rule.value === "string" ? rule.value : ""}
            onChange={(e) => updateField({ value: e.target.value })}
            className="rounded border border-neutral-300 px-1 py-0.5 dark:border-neutral-600 dark:bg-neutral-800"
          >
            <option value="">选择标签</option>
            {Object.keys(facets?.tags ?? {}).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
      );
    case "category":
      return (
        <div className="flex items-center gap-2 text-sm">
          <span>分类</span>
          <select
            value={typeof rule.value === "string" ? rule.value : ""}
            onChange={(e) => updateField({ value: e.target.value })}
            className="rounded border border-neutral-300 px-1 py-0.5 dark:border-neutral-600 dark:bg-neutral-800"
          >
            <option value="">选择分类</option>
            {Object.keys(facets?.categories ?? {}).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
      );
    case "media_type":
      return (
        <div className="flex items-center gap-2 text-sm">
          <span>类型</span>
          <select
            value={typeof rule.value === "string" ? rule.value : "image"}
            onChange={(e) => updateField({ value: e.target.value })}
            className="rounded border border-neutral-300 px-1 py-0.5 dark:border-neutral-600 dark:bg-neutral-800"
          >
            <option value="image">图片</option>
            <option value="video">视频</option>
            <option value="other">其他</option>
          </select>
        </div>
      );
    case "favorite":
      return (
        <div className="flex items-center gap-2 text-sm">
          <span>收藏</span>
          <select
            value={rule.value === true ? "true" : "false"}
            onChange={(e) => updateField({ value: e.target.value === "true" })}
            className="rounded border border-neutral-300 px-1 py-0.5 dark:border-neutral-600 dark:bg-neutral-800"
          >
            <option value="true">已收藏</option>
            <option value="false">未收藏</option>
          </select>
        </div>
      );
    case "time":
      return (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>时间</span>
          <input
            type="datetime-local"
            value={epochToLocalInput(rule.after) ?? ""}
            onChange={(e) => updateField({ after: localInputToEpoch(e.target.value) })}
            className="rounded border border-neutral-300 px-1 py-0.5 text-sm dark:border-neutral-600 dark:bg-neutral-800"
          />
          <span>至</span>
          <input
            type="datetime-local"
            value={epochToLocalInput(rule.before) ?? ""}
            onChange={(e) => updateField({ before: localInputToEpoch(e.target.value) })}
            className="rounded border border-neutral-300 px-1 py-0.5 text-sm dark:border-neutral-600 dark:bg-neutral-800"
          />
        </div>
      );
    default:
      return null;
  }
}

function isValidSmartRule(rule: SmartAlbumRule): boolean {
  if (rule.op === "and" || rule.op === "or") {
    return (rule.children ?? []).length > 0 && (rule.children ?? []).every(isValidSmartRule);
  }
  if (rule.op === "person") return rule.person_id != null;
  if (rule.op === "place") return rule.place_id != null && rule.place_id !== "";
  if (rule.op === "tag" || rule.op === "category" || rule.op === "media_type") {
    return typeof rule.value === "string" && rule.value !== "";
  }
  if (rule.op === "favorite") return typeof rule.value === "boolean";
  if (rule.op === "time") return rule.after != null || rule.before != null;
  return false;
}

function epochToLocalInput(epoch?: number): string | undefined {
  if (epoch == null) return undefined;
  const d = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function localInputToEpoch(value: string): number | undefined {
  if (!value) return undefined;
  return Math.floor(new Date(value).getTime() / 1000);
}

export default function Albums() {
  const { id } = useParams();
  return id != null ? <AlbumDetail /> : <AlbumList />;
}
