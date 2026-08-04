import { useRef, useState } from "react";

async function jfetch<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/uploads${path}`, init);
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(
      `${res.status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`,
    );
  }
  return res.json() as Promise<T>;
}

interface Session {
  upload_id: string;
  chunk_size: number;
  size: number;
}

export default function Upload() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<
    "idle" | "creating" | "uploading" | "done" | "error"
  >("idle");
  const [msg, setMsg] = useState("");
  const [progress, setProgress] = useState(0);

  const totalChunks = useRef(0);
  const doneChunks = useRef(0);

  async function upload() {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setMsg("请选择一个文件");
      return;
    }
    setState("creating");
    setMsg("创建上传会话…");
    try {
      const session: Session = await jfetch("", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, size: file.size }),
      });
      const { upload_id, chunk_size } = session;
      totalChunks.current = Math.max(1, Math.ceil(file.size / chunk_size));
      doneChunks.current = 0;

      // Resume support: ask server which chunks are already present.
      const info = await jfetch<{
        uploaded_chunks: number[];
        finalized: boolean;
      }>(`/${upload_id}`);
      const have = new Set(info.uploaded_chunks);
      doneChunks.current = info.finalized ? totalChunks.current : have.size;

      setState("uploading");
      setMsg("上传分片中…");

      for (let idx = 0; idx < totalChunks.current; idx++) {
        if (have.has(idx)) continue;
        const start = idx * chunk_size;
        const end = Math.min(file.size, start + chunk_size);
        const blob = file.slice(start, end);

        const putRes = await jfetch(`/${upload_id}/chunks/${idx}`, {
          method: "PUT",
          body: (() => {
            const fd = new FormData();
            fd.append("file", blob, `chunk-${idx}`);
            return fd;
          })(),
        });
        void putRes;
        doneChunks.current++;
        setProgress((doneChunks.current / totalChunks.current) * 100);
      }

      setMsg("合并校验中…");
      const complete = await jfetch<{ filename: string; staging_path: string }>(
        `/${upload_id}/complete`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
      );

      // Trigger ingestion into the library.
      await fetch(`/api/uploads/${upload_id}/ingest`, { method: "POST" }).catch(
        (e) => console.warn("ingest warning:", e),
      );

      setState("done");
      setProgress(100);
      setMsg(`上传完成：${complete.filename}`);
    } catch (e) {
      setState("error");
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-4 text-xl font-semibold">上传（分片 + 断点续传）</h1>
      <div className="max-w-lg rounded-md border border-dashed border-neutral-300 p-6 dark:border-neutral-700">
        <input ref={fileRef} type="file" className="mb-4 block w-full text-sm" />
        <button
          onClick={upload}
          disabled={state === "uploading" || state === "creating"}
          className="rounded-md bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
        >
          {state === "creating" ? "创建会话…" : state === "uploading" ? "上传中…" : "开始上传"}
        </button>

        {(state === "uploading" || state === "done") && (
          <div className="mt-4">
            <div className="mb-1 text-xs text-neutral-500">{Math.round(progress)}%</div>
            <div className="h-2 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-700">
              <div
                className="h-full rounded-full bg-brand-500 transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        {msg && (
          <p
            className={`mt-3 text-sm ${
              state === "error" ? "text-red-500" : "text-neutral-500"
            }`}
          >
            {msg}
          </p>
        )}
      </div>
      <p className="mt-4 max-w-lg text-xs text-neutral-400">
        上传会按 4 MiB 分片，每片幂等（服务端已校验字节），中途断网后重新选择同一文件会
        自动从缺失的分片继续（断点续传）。完成后服务端校验总大小与哈希。
      </p>
    </div>
  );
}
