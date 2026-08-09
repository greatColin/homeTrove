import { useState } from "react";

export function MutedVideoPreview({ assetId }: { assetId: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <video
      src={`/api/assets/${assetId}/file`}
      muted
      loop
      autoPlay
      playsInline
      preload="auto"
      onError={() => setFailed(true)}
      className="pointer-events-none absolute inset-0 h-full w-full object-cover"
    />
  );
}
