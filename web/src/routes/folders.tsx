import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, mediaLabel, type FolderRoot } from "../lib/api";

export default function Folders() {
  const [expanded, setExpanded] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["folders"],
    queryFn: api.folders,
  });
  const { data: assets } = useQuery({
    queryKey: ["folder-assets", expanded],
    queryFn: () =>
      fetch(`/api/folders/assets?media_root=${encodeURIComponent(expanded!)}`).then((r) => r.json()),
    enabled: !!expanded,
  });

  return (
    <div className="p-4 md:p-6">
      <h1 className="mb-4 text-xl font-semibold">文件夹</h1>
      {isLoading && <p className="text-neutral-400">加载中…</p>}
      <div className="max-w-2xl space-y-1">
        {(data?.roots ?? []).map((f: FolderRoot) => (
          <div key={f.media_root}>
            <button
              onClick={() => setExpanded(expanded === f.media_root ? null : f.media_root)}
              className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left hover:bg-neutral-100 dark:hover:bg-neutral-800"
            >
              <span className="font-mono text-sm">{f.media_root}</span>
              <span className="text-xs text-neutral-500">
                {f.total} 项 · 图 {f.media_types.image} / 视频 {f.media_types.video}
              </span>
            </button>
            {expanded === f.media_root && (
              <div className="ml-4 rounded-md border-l border-neutral-200 pl-4 dark:border-neutral-700">
                <ul className="divide-y divide-neutral-100 text-xs dark:divide-neutral-800">
                  {(assets?.items ?? []).map((a: any) => (
                    <li key={a.id} className="py-1.5">
                      <span className="font-mono">{a.path}</span>
                      <span className="ml-2 rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-500 dark:bg-neutral-800">
                        {mediaLabel(a.media_type)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
