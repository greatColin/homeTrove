import { useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, mediaLabel, thumbUrl, type PlaceCluster } from "../lib/api";

const W = 900;
const H = 450;

function project(lat: number, lon: number): { x: number; y: number } {
  return { x: ((lon + 180) / 360) * W, y: ((90 - lat) / 180) * H };
}

function PlaceMap({
  clusters,
  selected,
  onSelect,
}: {
  clusters: PlaceCluster[];
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const max = Math.max(1, ...clusters.map((c) => c.count));
  return (
    <div className="relative overflow-hidden rounded-lg border border-neutral-200 bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-900">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        {[-120, -60, 0, 60, 120].map((lon) => (
          <line
            key={lon}
            x1={((lon + 180) / 360) * W}
            x2={((lon + 180) / 360) * W}
            y1={0}
            y2={H}
            stroke="#d4d4d4"
            strokeWidth={0.5}
          />
        ))}
        {[-60, -30, 0, 30, 60].map((lat) => (
          <line
            key={lat}
            y1={((90 - lat) / 180) * H}
            y2={((90 - lat) / 180) * H}
            x1={0}
            x2={W}
            stroke="#d4d4d4"
            strokeWidth={0.5}
          />
        ))}
        {clusters.map((c) => {
          const { x, y } = project(c.lat, c.lon);
          const r = 4 + (c.count / max) * 12;
          const key = `${c.grid[0]},${c.grid[1]}`;
          const active = selected === key;
          return (
            <circle
              key={key}
              cx={x}
              cy={y}
              r={r}
              fill={active ? "#6366f1" : "rgba(99,102,241,0.55)"}
              stroke={active ? "#312e81" : "#c7d2fe"}
              strokeWidth={active ? 2 : 1}
              className="cursor-pointer transition hover:fill-indigo-500"
              onClick={() => onSelect(active ? "" : key)}
            >
              <title>{`${c.lat.toFixed(2)}, ${c.lon.toFixed(2)} — ${c.count} 项`}</title>
            </circle>
          );
        })}
      </svg>
      <p className="absolute left-2 top-2 rounded bg-black/50 px-1.5 py-0.5 text-xs text-white">
        等距圆柱投影 · 点击圆点查看该地资产
      </p>
    </div>
  );
}

function ClusterAssets({ cluster }: { cluster: PlaceCluster }) {
  const ids = cluster.asset_ids.slice(0, 24);
  const results = useQueries({
    queries: ids.map((aid) => ({
      queryKey: ["asset", aid],
      queryFn: () => api.asset(aid),
    })),
  });
  return (
    <div className="mt-2 grid grid-cols-3 gap-1 sm:grid-cols-4 lg:grid-cols-6">
      {results.map((r, i) =>
        r.data ? (
          <Link
            key={ids[i]}
            to={`/asset/${ids[i]}`}
            className="relative m-[2px] aspect-[4/3] overflow-hidden rounded-sm bg-neutral-200 dark:bg-neutral-800"
          >
            <img
              src={thumbUrl(ids[i], r.data.media_type === "video" ? "placeholder" : "small")}
              alt=""
              loading="lazy"
              className="h-full w-full object-cover"
              onError={(e) => {
                (e.currentTarget as HTMLImageElement).style.display = "none";
              }}
            />
            {r.data.media_type === "video" && (
              <span className="absolute inset-0 flex items-center justify-center text-xs text-white/90">
                <span className="rounded bg-black/50 px-2 py-0.5">
                  {mediaLabel(r.data.media_type)}
                </span>
              </span>
            )}
          </Link>
        ) : null,
      )}
    </div>
  );
}

export default function Places() {
  const { data } = useQuery({ queryKey: ["places"], queryFn: api.places });
  const [selected, setSelected] = useState<string | null>(null);

  const clusters = useMemo(() => data?.items ?? [], [data]);
  const selectedCluster = useMemo(
    () => clusters.find((c) => `${c.grid[0]},${c.grid[1]}` === selected),
    [clusters, selected],
  );

  return (
    <div className="p-4 md:p-6">
      <h1 className="text-xl font-semibold">地点</h1>
      <p className="mt-1 text-sm text-neutral-500">
        依据 EXIF 中的 GPS 信息，按约 {(data?.grid ?? 0.5).toFixed(1)}° 网格聚类。
      </p>

      {clusters.length === 0 ? (
        <p className="mt-6 text-sm text-neutral-500">
          尚未发现带 GPS 坐标的照片。带位置信息的照片扫描并提取 EXIF 后会显示在这里。
        </p>
      ) : (
        <>
          <div className="mt-4">
            <PlaceMap clusters={clusters} selected={selected} onSelect={setSelected} />
          </div>

          {selectedCluster && (
            <div className="mt-4 rounded-lg border border-neutral-200 p-3 dark:border-neutral-700">
              <p className="text-sm font-medium">
                {selectedCluster.lat.toFixed(3)}, {selectedCluster.lon.toFixed(3)} —
                {selectedCluster.count} 项
              </p>
              <ClusterAssets cluster={selectedCluster} />
            </div>
          )}

          <h2 className="mt-6 text-sm font-medium text-neutral-500">全部聚类</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {clusters.map((c) => {
              const key = `${c.grid[0]},${c.grid[1]}`;
              const active = selected === key;
              return (
                <button
                  key={key}
                  onClick={() => setSelected(active ? "" : key)}
                  className={`rounded-full border px-3 py-1 text-sm ${
                    active
                      ? "border-indigo-500 bg-indigo-500 text-white"
                      : "border-neutral-300 hover:border-indigo-400 dark:border-neutral-600"
                  }`}
                >
                  {c.lat.toFixed(2)}, {c.lon.toFixed(2)} · {c.count}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
