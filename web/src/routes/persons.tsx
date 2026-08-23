import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  api,
  mediaLabel,
  recognizeFace,
  type AssetDTO,
  type ClusterSummaryDTO,
  type PersonDTO,
  type RecognizeResult,
} from "../lib/api";

function PersonThumb({ person }: { person: PersonDTO }) {
  const firstAssetId = person.asset_ids?.[0];
  return (
    <div className="flex h-24 w-24 items-center justify-center overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
      {firstAssetId != null ? (
        <img
          src={`/api/assets/${firstAssetId}/file`}
          alt=""
          loading="lazy"
          className="h-full w-full object-cover"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
        />
      ) : (
        <span className="text-neutral-400">?</span>
      )}
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  "face.image": "图片",
  "face.video": "视频",
};

function ClusterBadge({ cluster }: { cluster: ClusterSummaryDTO }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
      <span className="font-medium">
        {SOURCE_LABEL[cluster.source_plugin_id] ?? cluster.source_plugin_id}
      </span>
      <span className="text-neutral-400">·</span>
      <span>{cluster.face_count} 张</span>
    </span>
  );
}

function PersonCard({
  person,
  selected,
  onSelect,
}: {
  person: PersonDTO;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  const isUnnamed = person.name.startsWith("未命名");
  return (
    <button
      onClick={() => onSelect(person.id)}
      className={`flex flex-col items-center gap-3 rounded-lg border p-4 text-center transition ${
        selected
          ? "border-brand-500 bg-brand-50 dark:bg-brand-900/40"
          : "border-neutral-200 bg-white hover:border-neutral-300 dark:border-neutral-700 dark:bg-neutral-900 dark:hover:border-neutral-600"
      }`}
    >
      <PersonThumb person={person} />
      <div>
        <div className="font-medium text-neutral-900 dark:text-neutral-100">
          {isUnnamed ? (
            <span className="italic text-neutral-400">{person.name}</span>
          ) : (
            person.name
          )}
        </div>
        <div className="mt-1 flex flex-wrap justify-center gap-1">
          {(person.clusters ?? []).map((c) => (
            <ClusterBadge key={c.id} cluster={c} />
          ))}
          {person.clusters == null && (
            <span className="text-xs text-neutral-500">
              {person.face_count} 张人脸
            </span>
          )}
        </div>
      </div>
    </button>
  );
}

function EditDialog({
  person,
  onClose,
}: {
  person: PersonDTO;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(person.name);
  const [info, setInfo] = useState(JSON.stringify(person.info ?? {}, null, 2));
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => {
      let parsed: Record<string, unknown> = {};
      if (info.trim()) {
        parsed = JSON.parse(info);
      }
      return api.updatePerson(person.id, {
        name: name.trim() || undefined,
        info: parsed,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["persons"] });
      qc.invalidateQueries({ queryKey: ["clusters"] });
      qc.invalidateQueries({ queryKey: ["facets"] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg bg-white p-5 dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-semibold">编辑人物</h2>
        <label className="mb-2 block text-sm text-neutral-600 dark:text-neutral-300">
          姓名
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="输入姓名，保存后自动反扫相似人脸"
            className="mt-1 w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-800"
          />
        </label>
        <label className="mb-2 block text-sm text-neutral-600 dark:text-neutral-300">
          附加信息（JSON，自由录入）
          <textarea
            value={info}
            onChange={(e) => setInfo(e.target.value)}
            rows={5}
            className="mt-1 w-full rounded border border-neutral-300 bg-white px-3 py-2 font-mono text-xs dark:border-neutral-700 dark:bg-neutral-800"
          />
        </label>
        {error && <p className="mb-2 text-sm text-red-500">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            取消
          </button>
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="rounded bg-brand-500 px-3 py-1.5 text-sm text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {mutation.isPending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function MergeDialog({
  person,
  onClose,
}: {
  person: PersonDTO;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["persons"], queryFn: () => api.persons() });
  const unnamed = (data?.items ?? []).filter(
    (p) => p.id !== person.id && p.name.startsWith("未命名"),
  );
  const [target, setTarget] = useState<number | undefined>(unnamed[0]?.id);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.mergePersons(person.id, target!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["persons"] });
      qc.invalidateQueries({ queryKey: ["clusters"] });
      qc.invalidateQueries({ queryKey: ["facets"] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg bg-white p-5 dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-semibold">合并到「{person.name}」</h2>
        <p className="mb-3 text-sm text-neutral-500">
          选择要合并进来的未命名人物，其所有人脸将并入「{person.name}」。
        </p>
        <select
          value={target ?? ""}
          onChange={(e) => setTarget(Number(e.target.value))}
          className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-800"
        >
          {unnamed.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}（{p.face_count} 张）
            </option>
          ))}
        </select>
        {unnamed.length === 0 && (
          <p className="mt-2 text-sm text-neutral-400">当前没有可合并的未命名人物</p>
        )}
        {error && <p className="mb-2 mt-2 text-sm text-red-500">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            取消
          </button>
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || target == null}
            className="rounded bg-brand-500 px-3 py-1.5 text-sm text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {mutation.isPending ? "合并中…" : "确认合并"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PersonClusters({
  person,
  onEdit,
  onMerge,
}: {
  person: PersonDTO;
  onEdit: (p: PersonDTO) => void;
  onMerge: (p: PersonDTO) => void;
}) {
  const clusters = person.clusters ?? [];
  return (
    <div className="mt-8">
      <div className="mb-3 flex items-baseline gap-3">
        <h2 className="text-lg font-semibold">「{person.name}」的识别组</h2>
        <span className="text-sm text-neutral-500">{clusters.length} 个识别组</span>
        <button
          onClick={() => onEdit(person)}
          className="ml-auto rounded bg-brand-50 px-2 py-1 text-xs text-brand-600 hover:bg-brand-100 dark:bg-brand-900/40 dark:text-brand-300"
        >
          编辑
        </button>
        <button
          onClick={() => onMerge(person)}
          disabled={person.name.startsWith("未命名")}
          className="rounded bg-neutral-100 px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-200 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-700"
          title={person.name.startsWith("未命名") ? "请先命名该人物" : "合并未命名人物到此"}
        >
          合并
        </button>
      </div>
      {clusters.length === 0 ? (
        <p className="mt-4 text-center text-neutral-400">该人物暂无识别组</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {clusters.map((c) => (
            <Link
              key={c.id}
              to={`/clusters/${c.id}`}
              className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-white p-3 transition hover:border-brand-500 hover:bg-brand-50 dark:border-neutral-700 dark:bg-neutral-900 dark:hover:border-brand-500 dark:hover:bg-brand-900/40"
            >
              <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-full bg-neutral-100 dark:bg-neutral-800">
                <span className="text-xs font-medium text-neutral-600 dark:text-neutral-300">
                  {SOURCE_LABEL[c.source_plugin_id] ?? c.source_plugin_id}
                </span>
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-neutral-900 dark:text-neutral-100">
                  {c.name}
                </div>
                <div className="text-xs text-neutral-500">
                  {c.face_count} 张人脸 · 半径 {c.radius.toFixed(3)}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function AssignClusterDialog({
  cluster,
  onClose,
}: {
  cluster: ClusterSummaryDTO;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["persons"], queryFn: () => api.persons() });
  const [target, setTarget] = useState<number | undefined>(undefined);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const assignMutation = useMutation({
    mutationFn: (personId: number) => api.updateCluster(cluster.id, { person_id: personId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["clusters"] });
      qc.invalidateQueries({ queryKey: ["persons"] });
      qc.invalidateQueries({ queryKey: ["facets"] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  const createMutation = useMutation({
    mutationFn: (name: string) => api.createPerson(name),
    onSuccess: (person) => {
      setCreating(false);
      setNewName("");
      assignMutation.mutate(person.id);
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg bg-white p-5 dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-semibold">归入人物</h2>
        <p className="mb-3 text-sm text-neutral-500">
          选择要将「{cluster.name}」（{cluster.face_count} 张人脸）归入哪个人物，或创建新人物。
        </p>

        {!creating ? (
          <>
            <select
              value={target ?? ""}
              onChange={(e) => setTarget(Number(e.target.value) || undefined)}
              className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-800"
            >
              <option value="">— 选择人物 —</option>
              {(data?.items ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}（{p.face_count} 张人脸）
                </option>
              ))}
            </select>
            <button
              onClick={() => setCreating(true)}
              className="mt-2 w-full rounded border border-dashed border-brand-400 bg-brand-50 px-3 py-2 text-sm text-brand-600 hover:bg-brand-100 dark:border-brand-600 dark:bg-brand-900/20 dark:text-brand-400"
            >
              + 创建新人物
            </button>
          </>
        ) : (
          <div className="flex gap-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="输入人物姓名"
              autoFocus
              className="flex-1 rounded border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-800"
              onKeyDown={(e) => {
                if (e.key === "Enter" && newName.trim()) createMutation.mutate(newName.trim());
                if (e.key === "Escape") setCreating(false);
              }}
            />
            <button
              onClick={() => newName.trim() && createMutation.mutate(newName.trim())}
              disabled={createMutation.isPending || !newName.trim()}
              className="rounded bg-brand-500 px-3 py-2 text-sm text-white hover:bg-brand-600 disabled:opacity-50"
            >
              {createMutation.isPending ? "创建中…" : "创建"}
            </button>
            <button
              onClick={() => setCreating(false)}
              className="rounded bg-neutral-200 px-3 py-2 text-sm hover:bg-neutral-300 dark:bg-neutral-700 dark:hover:bg-neutral-600"
            >
              取消
            </button>
          </div>
        )}

        {error && <p className="mb-2 mt-2 text-sm text-red-500">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            取消
          </button>
          <button
            onClick={() => target != null && assignMutation.mutate(target)}
            disabled={assignMutation.isPending || target == null}
            className="rounded bg-brand-500 px-3 py-1.5 text-sm text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {assignMutation.isPending ? "归入中…" : "确认归入"}
          </button>
        </div>
      </div>
    </div>
  );
}

function RecognizeDialog({ onClose, onAssignCluster }: { onClose: () => void; onAssignCluster?: (c: ClusterSummaryDTO) => void }) {
  const [preview, setPreview] = useState<string | null>(null);
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null);
  const [result, setResult] = useState<RecognizeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  let videoRef: HTMLVideoElement | null = null;
  let canvasRef: HTMLCanvasElement | null = null;

  const handleFile = (file: File) => {
    const url = URL.createObjectURL(file);
    setPreview(url);
    setResult(null);
    setError(null);
    stopCamera();
  };

  const stopCamera = () => {
    if (cameraStream) {
      cameraStream.getTracks().forEach((t) => t.stop());
      setCameraStream(null);
    }
  };

  const startCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" } });
      setCameraStream(stream);
      setPreview(null);
      setResult(null);
      setError(null);
    } catch {
      setError("无法访问摄像头");
    }
  };

  const captureFromCamera = () => {
    if (!videoRef || !canvasRef) return;
    canvasRef.width = videoRef.videoWidth;
    canvasRef.height = videoRef.videoHeight;
    canvasRef.getContext("2d")?.drawImage(videoRef, 0, 0);
    canvasRef.toBlob((blob) => {
      if (blob) {
        const file = new File([blob], "camera.jpg", { type: "image/jpeg" });
        handleFile(file);
        handleRecognize(file);
      }
    });
  };

  const handleRecognize = async (file: File) => {
    setLoading(true);
    setError(null);
    try {
      const res = await recognizeFace(file);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "识别失败");
    } finally {
      setLoading(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith("image/")) {
      handleFile(file);
      handleRecognize(file);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg bg-white p-5 dark:bg-neutral-900" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-4 text-lg font-semibold">图片识别</h2>

        {loading && (
          <div className="flex flex-col items-center gap-3 py-8">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
            <p className="text-neutral-500">识别中…</p>
          </div>
        )}

        {!loading && cameraStream && (
          <div className="flex flex-col items-center gap-3">
            <video ref={(el) => { videoRef = el; if (el && cameraStream) el.srcObject = cameraStream; }} autoPlay playsInline className="w-full max-w-sm rounded" />
            <canvas ref={(el) => { canvasRef = el; }} className="hidden" />
            <div className="flex gap-2">
              <button onClick={captureFromCamera} className="rounded bg-brand-500 px-4 py-2 text-white hover:bg-brand-600">拍照</button>
              <button onClick={stopCamera} className="rounded bg-neutral-200 px-4 py-2 hover:bg-neutral-300 dark:bg-neutral-700 dark:hover:bg-neutral-600">取消</button>
            </div>
          </div>
        )}

        {!loading && preview && (
          <div className="flex flex-col items-center gap-3">
            <img src={preview} alt="preview" className="w-full max-w-sm rounded" />
            <button onClick={() => { setPreview(null); setResult(null); }} className="rounded bg-neutral-200 px-4 py-2 hover:bg-neutral-300 dark:bg-neutral-700 dark:hover:bg-neutral-600">重选图片</button>
          </div>
        )}

        {!loading && !cameraStream && !preview && (
          <div
            className="flex flex-col items-center gap-3"
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
          >
            <div className="flex w-full gap-3">
              <label className="flex-1 cursor-pointer rounded border border-neutral-300 bg-neutral-50 py-6 text-center text-sm text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
                <span>点击上传图片</span>
                <input type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) { handleFile(f); handleRecognize(f); } }} />
              </label>
              <button onClick={startCamera} className="flex-1 rounded border border-neutral-300 bg-neutral-50 py-6 text-sm text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
                使用摄像头
              </button>
            </div>
          </div>
        )}

        {error && <p className="mt-3 text-sm text-red-500">{error}</p>}

        {result && (
          <div className="mt-4">
            {result.total_detected === 0 ? (
              <p className="py-4 text-center text-neutral-500">未检测到人脸。请尝试上传包含人脸的照片。</p>
            ) : (
              <>
                <p className="mb-2 text-sm text-neutral-500">检测到 {result.total_detected} 张人脸，相似人物如下：</p>
                <div className="max-h-64 space-y-2 overflow-y-auto">
                  {result.faces.map((face, fi) => (
                    <div key={fi} className="rounded border border-neutral-200 p-2 dark:border-neutral-700">
                      <p className="text-xs text-neutral-500">人脸 #{fi + 1}（检测置信度 {(face.det_score * 100).toFixed(0)}%）</p>
                      {face.matches.length > 0 ? (
                        <div className="mt-1 flex flex-wrap gap-2">
                          {face.matches.map((m) =>
                            m.person_id != null ? (
                              <Link
                                key={m.person_id}
                                to={`/persons/${m.person_id}`}
                                onClick={onClose}
                                className="flex items-center gap-2 rounded bg-neutral-100 px-2 py-1 text-sm hover:bg-neutral-200 dark:bg-neutral-800 dark:hover:bg-neutral-700"
                              >
                                {m.representative_face_asset_id ? (
                                  <img
                                    src={`/api/assets/${m.representative_face_asset_id}/thumbnail?size=small`}
                                    alt=""
                                    className="h-6 w-6 rounded-full object-cover"
                                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
                                  />
                                ) : (
                                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-neutral-300 text-xs">?</div>
                                )}
                                <span className="font-medium">{m.name}</span>
                                <span className="text-xs text-brand-600">{(m.avg_score * 100).toFixed(1)}%</span>
                              </Link>
                            ) : (
                              <div
                                key={`cluster-${m.cluster_id}`}
                                className="flex items-center gap-2 rounded bg-neutral-100 px-2 py-1 text-sm"
                              >
                                {m.representative_face_asset_id ? (
                                  <img
                                    src={`/api/assets/${m.representative_face_asset_id}/thumbnail?size=small`}
                                    alt=""
                                    className="h-6 w-6 rounded-full object-cover"
                                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
                                  />
                                ) : (
                                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-neutral-300 text-xs">?</div>
                                )}
                                <span className="text-neutral-600">{m.name}</span>
                                <span className="text-xs text-neutral-400">{(m.avg_score * 100).toFixed(1)}%</span>
                                {m.cluster_id && onAssignCluster && (
                                  <button
                                    onClick={() => {
                                      const fakeCluster: ClusterSummaryDTO = {
                                        id: m.cluster_id!,
                                        name: m.name,
                                        source_plugin_id: "",
                                        source_model_name: "",
                                        face_count: m.matched_count,
                                        radius: 0,
                                      };
                                      onAssignCluster(fakeCluster);
                                      onClose();
                                    }}
                                    className="ml-1 rounded bg-brand-100 px-1.5 py-0.5 text-xs text-brand-600 hover:bg-brand-200 dark:bg-brand-900/40 dark:text-brand-300"
                                  >
                                    归入人物
                                  </button>
                                )}
                              </div>
                            ),
                          )}
                        </div>
                      ) : (
                        <p className="mt-1 text-xs text-neutral-400">无匹配人物。扫描后系统会自动创建识别组，可在人物页面归入已有人物或新建人物。</p>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        <div className="mt-4 flex justify-end">
          <button onClick={onClose} className="rounded px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800">
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

function UnassignedClusters({ onAssign }: { onAssign: (c: ClusterSummaryDTO) => void }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["clusters", { unassigned: true }],
    queryFn: () => api.clusters({ unassigned: true }),
  });
  const items = data?.items ?? [];
  return (
    <>
      <div className="mt-8">
        <div className="mb-3 flex items-baseline gap-3">
          <h2 className="text-lg font-semibold">未归入人物的识别组</h2>
          <span className="text-sm text-neutral-500">{items.length} 个</span>
          <button
            onClick={() => qc.invalidateQueries({ queryKey: ["clusters"] })}
            className="ml-auto rounded bg-neutral-100 px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-700"
          >
            刷新
          </button>
        </div>
      {isLoading ? (
        <p className="mt-4 text-center text-neutral-400">加载中…</p>
      ) : items.length === 0 ? (
        <p className="mt-4 text-center text-neutral-400">暂无未归类的识别组</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((c) => (
            <div
              key={c.id}
              className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-white p-3 transition hover:border-brand-500 hover:bg-brand-50 dark:border-neutral-700 dark:bg-neutral-900 dark:hover:border-brand-500 dark:hover:bg-brand-900/40"
            >
              <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center overflow-hidden rounded-full bg-neutral-100 dark:bg-neutral-800">
                {c.representative_face ? (
                  <img
                    src={`/api/assets/${c.representative_face.asset_id}/thumbnail`}
                    alt=""
                    className="h-full w-full object-cover"
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).style.display = "none";
                    }}
                  />
                ) : (
                  <span className="text-xs font-medium text-neutral-600 dark:text-neutral-300">
                    {SOURCE_LABEL[c.source_plugin_id] ?? c.source_plugin_id}
                  </span>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-neutral-900 dark:text-neutral-100">
                  {c.name}
                </div>
                <div className="text-xs text-neutral-500">
                  {c.face_count} 张人脸 · 半径 {c.radius.toFixed(3)}
                </div>
              </div>
              <div className="flex flex-col gap-1">
                <Link
                  to={`/clusters/${c.id}`}
                  className="rounded bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-700"
                >
                  查看
                </Link>
                <button
                  onClick={() => onAssign(c)}
                  className="rounded bg-brand-50 px-2 py-0.5 text-xs text-brand-600 hover:bg-brand-100 dark:bg-brand-900/40 dark:text-brand-300 dark:hover:bg-brand-900/60"
                >
                  归入人物
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
    </>
  );
}

export default function PersonsPage() {
  const params = useParams<{ personId: string }>();
  const { data, isLoading } = useQuery({
    queryKey: ["persons", { includeClusters: true }],
    queryFn: () => api.persons(true),
  });
  const [editing, setEditing] = useState<PersonDTO | null>(null);
  const [merging, setMerging] = useState<PersonDTO | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [assigning, setAssigning] = useState<ClusterSummaryDTO | null>(null);
  const [recognizing, setRecognizing] = useState(false);

  useEffect(() => {
    if (params.personId) {
      setSelectedId(Number(params.personId));
    }
  }, [params.personId]);

  const people = data?.items ?? [];
  const selected = people.find((p) => p.id === selectedId) ?? null;

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">人物</h1>
        <button
          onClick={() => setRecognizing(true)}
          className="rounded bg-brand-500 px-3 py-1.5 text-sm text-white hover:bg-brand-600"
        >
          图片识别
        </button>
      </div>
      <p className="mt-1 text-sm text-neutral-500">
        按识别组自动归组。点击人物查看其识别组，进入后可看到具体人脸并完成命名、合并、删除。
      </p>

      {isLoading ? (
        <p className="mt-8 text-center text-neutral-400">加载中…</p>
      ) : people.length === 0 ? (
        <p className="mt-8 text-center text-neutral-400">
          暂无人物。扫描并处理后，系统会按人脸自动归组。
        </p>
      ) : (
        <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
          {people.map((p) => (
            <PersonCard
              key={p.id}
              person={p}
              selected={p.id === selectedId}
              onSelect={setSelectedId}
            />
          ))}
        </div>
      )}

      {selected && (
        <PersonClusters
          person={selected}
          onEdit={setEditing}
          onMerge={setMerging}
        />
      )}

      <UnassignedClusters onAssign={setAssigning} />

      {editing && <EditDialog person={editing} onClose={() => setEditing(null)} />}
      {merging && <MergeDialog person={merging} onClose={() => setMerging(null)} />}
      {assigning && <AssignClusterDialog cluster={assigning} onClose={() => setAssigning(null)} />}
      {recognizing && <RecognizeDialog onClose={() => setRecognizing(false)} onAssignCluster={setAssigning} />}
    </div>
  );
}