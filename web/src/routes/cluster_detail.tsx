import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  api,
  mediaLabel,
  type AssetDTO,
  type ClusterDTO,
  type FaceDTO,
} from "../lib/api";

function FaceThumb({ face }: { face: FaceDTO }) {
  return (
    <div className="group relative m-[2px] aspect-[4/3] overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800">
      <img
        src={`/api/assets/${face.asset_id}/file`}
        alt=""
        loading="lazy"
        className="h-full w-full object-cover transition group-hover:scale-105"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.display = "none";
        }}
      />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-1 text-[10px] text-white">
        {face.frame_index != null && (
          <span>帧 {face.frame_index} · t={face.frame_t?.toFixed(2)}s</span>
        )}
        {face.confidence != null && (
          <span className="ml-1 opacity-75">置信 {(face.confidence * 100).toFixed(0)}%</span>
        )}
      </div>
    </div>
  );
}

export default function ClusterDetailPage() {
  const params = useParams<{ clusterId: string }>();
  const clusterId = Number(params.clusterId);
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["cluster", clusterId],
    queryFn: () => api.cluster(clusterId),
    enabled: Number.isFinite(clusterId),
  });

  if (!Number.isFinite(clusterId)) {
    return <p className="p-6 text-red-500">无效的 cluster id</p>;
  }
  if (isLoading) {
    return <p className="p-6 text-neutral-400">加载中…</p>;
  }
  if (error || !data) {
    return (
      <div className="p-6">
        <p className="text-red-500">加载失败：{(error as Error)?.message ?? "未知错误"}</p>
        <Link to="/persons" className="mt-2 inline-block text-brand-500 hover:underline">
          ← 返回人物列表
        </Link>
      </div>
    );
  }

  return <ClusterBody cluster={data} onRefresh={() => qc.invalidateQueries({ queryKey: ["cluster", clusterId] })} />;
}

function ClusterBody({ cluster, onRefresh }: { cluster: ClusterDTO; onRefresh: () => void }) {
  const qc = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(cluster.name);
  const [error, setError] = useState<string | null>(null);

  const renameMutation = useMutation({
    mutationFn: () => api.updateCluster(cluster.id, { name: name.trim() || cluster.name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cluster", cluster.id] });
      qc.invalidateQueries({ queryKey: ["clusters"] });
      qc.invalidateQueries({ queryKey: ["persons"] });
      setRenaming(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteCluster(cluster.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["clusters"] });
      qc.invalidateQueries({ queryKey: ["persons"] });
      qc.invalidateQueries({ queryKey: ["facets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const SOURCE_LABEL: Record<string, string> = {
    "face.image": "图片",
    "face.video": "视频",
  };

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-baseline gap-3">
        <Link
          to={cluster.person_id ? `/persons/${cluster.person_id}` : "/persons"}
          className="text-sm text-neutral-500 hover:text-brand-500"
        >
          ← 返回
        </Link>
        <h1 className="text-xl font-semibold">
          {renaming ? (
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              onBlur={() => setRenaming(false)}
              onKeyDown={(e) => {
                if (e.key === "Enter") renameMutation.mutate();
                if (e.key === "Escape") setRenaming(false);
              }}
              className="rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-800"
            />
          ) : (
            <button onClick={() => setRenaming(true)} className="hover:underline">
              {cluster.name}
            </button>
          )}
        </h1>
        <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
          {SOURCE_LABEL[cluster.source_plugin_id] ?? cluster.source_plugin_id}
        </span>
        <span className="text-xs text-neutral-500">
          模型 {cluster.source_model_name}
        </span>
        <button
          onClick={() => {
            if (confirm(`确定删除识别组「${cluster.name}」？其下所有人脸将被一并删除。`)) {
              deleteMutation.mutate();
            }
          }}
          disabled={deleteMutation.isPending}
          className="ml-auto rounded bg-red-50 px-2 py-1 text-xs text-red-600 hover:bg-red-100 disabled:opacity-50 dark:bg-red-900/40 dark:text-red-300"
        >
          {deleteMutation.isPending ? "删除中…" : "删除识别组"}
        </button>
      </div>

      <p className="mt-2 text-sm text-neutral-500">
        共 {cluster.face_count} 张人脸 · 半径 {cluster.radius.toFixed(3)}
        {cluster.person_id && (
          <span className="ml-2">
            · 归属于人物{" "}
            <Link
              to={`/persons/${cluster.person_id}`}
              className="text-brand-500 hover:underline"
            >
              #{cluster.person_id}
            </Link>
          </span>
        )}
      </p>

      {error && <p className="mt-2 text-sm text-red-500">{error}</p>}

      <div className="mt-6 grid grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
        {cluster.faces.map((f) => (
          <FaceTile key={f.id} face={f} clusterId={cluster.id} onDeleted={onRefresh} />
        ))}
      </div>

      {cluster.faces.length === 0 && (
        <p className="mt-4 text-center text-neutral-400">该识别组暂无人脸</p>
      )}
    </div>
  );
}

function FaceTile({
  face,
  clusterId,
  onDeleted,
}: {
  face: FaceDTO;
  clusterId: number;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const [preview, setPreview] = useState(false);
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteFace(face.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cluster", clusterId] });
      qc.invalidateQueries({ queryKey: ["clusters"] });
      qc.invalidateQueries({ queryKey: ["persons"] });
      onDeleted();
    },
  });

  return (
    <>
      <div className="group relative m-[2px] aspect-[4/3] overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800">
        <button
          onClick={() => setPreview(true)}
          className="block h-full w-full"
        >
          <img
            src={`/api/assets/${face.asset_id}/file`}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover transition group-hover:scale-105"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = "none";
            }}
          />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            if (confirm("确定删除这张人脸？")) deleteMutation.mutate();
          }}
          disabled={deleteMutation.isPending}
          className="absolute right-1 top-1 hidden rounded-full bg-white/90 px-2 py-0.5 text-[10px] text-red-600 hover:bg-red-50 group-hover:block dark:bg-neutral-900/90 dark:text-red-300"
        >
          删除
        </button>
        <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-1 text-[10px] text-white">
          {face.frame_index != null && (
            <span>帧 {face.frame_index} · t={face.frame_t?.toFixed(2)}s</span>
          )}
          {face.confidence != null && (
            <span className="ml-1 opacity-75">置信 {(face.confidence * 100).toFixed(0)}%</span>
          )}
        </div>
      </div>
      {preview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4"
          onClick={() => setPreview(false)}
        >
          <img
            src={`/api/assets/${face.asset_id}/file`}
            alt={face.asset_filename ?? ""}
            className="max-h-[90vh] max-w-full object-contain"
            onClick={(e) => e.stopPropagation()}
          />
          <Link
            to={`/asset/${face.asset_id}`}
            className="absolute right-4 top-4 rounded-full bg-white/10 px-3 py-1.5 text-sm text-white hover:bg-white/20"
          >
            详情
          </Link>
        </div>
      )}
    </>
  );
}