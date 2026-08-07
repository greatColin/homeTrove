import { useEffect, useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from "react-leaflet";
import { Link } from "react-router-dom";
import { api, mediaLabel, thumbUrl, type PlaceCluster } from "../lib/api";
import "leaflet/dist/leaflet.css";

function FitBounds({ clusters }: { clusters: PlaceCluster[] }) {
  const map = useMap();
  useEffect(() => {
    if (clusters.length === 0) return;
    const positions = clusters.map((c) => [c.lat, c.lon] as [number, number]);
    if (positions.length === 1) {
      map.setView(positions[0], 8);
    } else {
      map.fitBounds(positions, { padding: [24, 24] });
    }
  }, [map, clusters]);
  return null;
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
  const center: [number, number] = clusters[0]
    ? [clusters[0].lat, clusters[0].lon]
    : [0, 0];
  return (
    <div className="relative overflow-hidden rounded-lg border border-neutral-200 dark:border-neutral-700">
      <MapContainer
        center={center}
        zoom={3}
        scrollWheelZoom
        className="h-[450px] w-full"
        style={{ background: "#e5e7eb" }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {clusters.map((c) => {
          const r = 4 + (c.count / max) * 12;
          const key = `${c.grid[0]},${c.grid[1]}`;
          const active = selected === key;
          return (
            <CircleMarker
              key={key}
              center={[c.lat, c.lon]}
              radius={r}
              pathOptions={{
                color: active ? "#312e81" : "#c7d2fe",
                fillColor: active ? "#6366f1" : "rgba(99,102,241,0.55)",
                fillOpacity: 0.9,
                weight: active ? 2 : 1,
              }}
              eventHandlers={{ click: () => onSelect(active ? "" : key) }}
            >
              <Tooltip permanent direction="top" offset={[0, -r]}>
                <span>{c.count}</span>
              </Tooltip>
              <Tooltip direction="center" opacity={0.95}>
                <span>{`${c.lat.toFixed(2)}, ${c.lon.toFixed(2)} — ${c.count} 项`}</span>
              </Tooltip>
            </CircleMarker>
          );
        })}
        <FitBounds clusters={clusters} />
      </MapContainer>
      <p className="absolute left-2 top-2 z-[1000] rounded bg-black/50 px-1.5 py-0.5 text-xs text-white">
        Leaflet · OpenStreetMap · 点击圆点查看该地资产
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
  const { data, isError, refetch, isFetching } = useQuery({
    queryKey: ["places"],
    queryFn: api.places,
  });
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

      {isError ? (
        <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          <p>地点数据加载失败。</p>
          <button
            onClick={() => refetch()}
            className="mt-2 rounded bg-red-600 px-3 py-1 text-white hover:bg-red-700"
          >
            {isFetching ? "重试中…" : "重试"}
          </button>
        </div>
      ) : clusters.length === 0 ? (
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
