import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, mediaLabel, publicFileUrl, publicThumbUrl, type AssetDTO, type SharedAlbumDTO } from "../lib/api";
import { JustifiedGrid, type LayoutItem } from "../components/justified_grid";

export default function SharedAlbum() {
  const { token } = useParams();
  const [lightbox, setLightbox] = useState<number | null>(null);

  const album = useQuery<SharedAlbumDTO>({
    queryKey: ["public-album", token],
    queryFn: () => api.publicAlbum(token!),
    enabled: !!token,
  });

  if (album.isLoading) return <p className="p-6 text-neutral-400">加载中…</p>;
  if (album.isError || !album.data) {
    return (
      <div className="p-6 text-neutral-500">
        <h1 className="text-lg font-semibold">链接无效或已过期</h1>
        <p className="mt-1 text-sm">该分享链接不存在、已过期或已被撤销。</p>
      </div>
    );
  }

  const a = album.data;
  const ids = a.asset_ids;
  const placeholderAssets: AssetDTO[] = ids.map((id) => ({ id } as AssetDTO));

  return (
    <div className="flex h-full flex-col p-4 md:p-6">
      <div className="mb-4 shrink-0">
        <h1 className="text-xl font-semibold">{a.name}</h1>
        {a.description && <p className="mt-1 text-sm text-neutral-500">{a.description}</p>}
        <p className="mt-1 text-xs text-neutral-400">共 {ids.length} 项</p>
      </div>

      {ids.length === 0 ? (
        <p className="text-sm text-neutral-500">相册中没有可见的照片。</p>
      ) : (
        <div className="min-h-0 flex-1">
          <JustifiedGrid
            assets={placeholderAssets}
            renderItem={(item) => (
              <button
                key={item.asset.id}
                onClick={() => setLightbox(item.asset.id)}
                style={{
                  position: "absolute",
                  left: item.left,
                  top: item.top,
                  width: item.width,
                  height: item.height,
                }}
                className="overflow-hidden rounded-md bg-neutral-100 dark:bg-neutral-800"
              >
                <img
                  src={publicThumbUrl(token!, item.asset.id, "small")}
                  alt=""
                  loading="lazy"
                  className="h-full w-full object-cover"
                  onError={(e) => {
                    (e.currentTarget as HTMLImageElement).style.display = "none";
                  }}
                />
              </button>
            )}
            className="h-full"
          />
        </div>
      )}

      {lightbox != null && (
        <Lightbox token={token!} album={a} assetId={lightbox} onClose={() => setLightbox(null)} />
      )}
    </div>
  );
}

function Lightbox({
  token,
  album,
  assetId,
  onClose,
}: {
  token: string;
  album: SharedAlbumDTO;
  assetId: number;
  onClose: () => void;
}) {
  const idx = album.asset_ids.indexOf(assetId);
  const [current, setCurrent] = useState(idx >= 0 ? idx : 0);
  const currentAssetId = album.asset_ids[current];

  const prev = () => setCurrent((i) => (i > 0 ? i - 1 : album.asset_ids.length - 1));
  const next = () => setCurrent((i) => (i < album.asset_ids.length - 1 ? i + 1 : 0));

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") onClose();
    if (e.key === "ArrowLeft") prev();
    if (e.key === "ArrowRight") next();
  };

  const src = album.allow_original
    ? publicFileUrl(token, currentAssetId)
    : publicThumbUrl(token, currentAssetId, "medium");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4"
      onClick={onClose}
      onKeyDown={handleKey}
      tabIndex={0}
      autoFocus
    >
      <div className="absolute left-4 right-4 top-4 flex justify-between text-white/80">
        <span className="text-sm">
          {current + 1} / {album.asset_ids.length}
        </span>
        <button onClick={onClose} className="text-sm hover:text-white">
          关闭 ESC
        </button>
      </div>

      <div
        className="flex max-h-[85vh] max-w-[90vw] items-center justify-center"
        onClick={(e) => e.stopPropagation()}
      >
        <img
          src={src}
          alt=""
          className="max-h-[85vh] max-w-[85vw] object-contain"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
        />
      </div>

      {album.asset_ids.length > 1 && (
        <>
          <button
            onClick={(e) => {
              e.stopPropagation();
              prev();
            }}
            className="absolute left-2 top-1/2 -translate-y-1/2 rounded bg-white/20 px-3 py-2 text-white hover:bg-white/30"
          >
            ←
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              next();
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded bg-white/20 px-3 py-2 text-white hover:bg-white/30"
          >
            →
          </button>
        </>
      )}

      {album.allow_original && (
        <div className="absolute bottom-4 left-0 right-0 text-center">
          <a
            href={publicFileUrl(token, currentAssetId)}
            download={album.allow_download}
            onClick={(e) => e.stopPropagation()}
            className="rounded bg-white/20 px-4 py-2 text-sm text-white hover:bg-white/30"
          >
            {album.allow_download ? "下载原图" : "查看原图"}
          </a>
        </div>
      )}
    </div>
  );
}
