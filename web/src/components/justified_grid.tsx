import { useEffect, useMemo, useRef, useState } from "react";
import type { AssetDTO } from "../lib/api";

export interface LayoutItem {
  asset: AssetDTO;
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Row {
  top: number;
  height: number;
  width: number;
  items: LayoutItem[];
}

export interface JustifiedGridProps {
  assets: AssetDTO[];
  targetRowHeight?: number;
  gap?: number;
  overscan?: number;
  renderItem: (item: LayoutItem) => React.ReactNode;
  empty?: React.ReactNode;
  className?: string;
  headerHeight?: number;
}

function aspectRatio(a: AssetDTO): number {
  if (a.width && a.height && a.height > 0) {
    return a.width / a.height;
  }
  return 4 / 3;
}

export function buildLayout(
  assets: AssetDTO[],
  containerWidth: number,
  targetRowHeight: number,
  gap: number,
): Row[] {
  if (containerWidth <= 0 || assets.length === 0) return [];

  const rows: Row[] = [];
  let current: AssetDTO[] = [];
  let currentWidth = 0;
  let top = 0;

  function flushRow() {
    if (current.length === 0) return;
    const rowGap = gap * Math.max(0, current.length - 1);
    const available = containerWidth - rowGap;
    const rawWidth = currentWidth;
    const scale = rawWidth > 0 ? available / rawWidth : 1;
    const rowHeight = Math.min(targetRowHeight * scale, targetRowHeight * 2);

    let left = 0;
    const items: LayoutItem[] = current.map((a) => {
      const w = aspectRatio(a) * rowHeight;
      const item: LayoutItem = {
        asset: a,
        left,
        top: 0,
        width: w,
        height: rowHeight,
      };
      left += w + gap;
      return item;
    });

    rows.push({
      top,
      height: rowHeight,
      width: containerWidth,
      items,
    });
    top += rowHeight + gap;
    current = [];
    currentWidth = 0;
  }

  for (const a of assets) {
    const ar = aspectRatio(a);
    const w = ar * targetRowHeight;

    if (current.length > 0 && currentWidth + gap + w > containerWidth) {
      flushRow();
    }

    current.push(a);
    currentWidth += (current.length > 1 ? gap : 0) + w;
  }
  flushRow();

  // Trim the trailing gap from total height.
  if (rows.length > 0) {
    const last = rows[rows.length - 1];
    last.height -= gap;
  }

  return rows;
}

export function JustifiedGrid({
  assets,
  targetRowHeight = 200,
  gap = 4,
  overscan = 3,
  renderItem,
  empty,
  className = "",
  headerHeight = 0,
}: JustifiedGridProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const updateSize = () => {
      const rect = el.getBoundingClientRect();
      setContainerWidth(rect.width);
      setViewportHeight(window.innerHeight);
    };

    updateSize();
    const ro = new ResizeObserver(updateSize);
    ro.observe(el);
    window.addEventListener("resize", updateSize);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", updateSize);
    };
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => setScrollTop(el.scrollTop);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const rows = useMemo(
    () => buildLayout(assets, containerWidth, targetRowHeight, gap),
    [assets, containerWidth, targetRowHeight, gap],
  );

  const totalHeight = useMemo(() => {
    if (rows.length === 0) return 0;
    const last = rows[rows.length - 1];
    return last.top + last.height + headerHeight;
  }, [rows, headerHeight]);

  const { start, end } = useMemo(() => {
    if (rows.length === 0) return { start: 0, end: 0 };
    const startIdx = Math.max(
      0,
      rows.findIndex((r) => r.top + r.height >= scrollTop) - overscan,
    );
    const bottom = scrollTop + viewportHeight;
    let endIdx = rows.length - 1;
    for (let i = startIdx; i < rows.length; i++) {
      if (rows[i].top > bottom) {
        endIdx = Math.min(rows.length - 1, i + overscan - 1);
        break;
      }
    }
    return { start: startIdx, end: endIdx };
  }, [rows, scrollTop, viewportHeight, overscan]);

  if (assets.length === 0 && empty) {
    return <div className={className}>{empty}</div>;
  }

  return (
    <div
      ref={containerRef}
      className={`relative overflow-y-auto ${className}`}
      style={{ height: "100%" }}
    >
      <div style={{ height: totalHeight, position: "relative" }}>
        {rows.slice(start, end + 1).map((row) => (
          <div
            key={row.items.map((i) => i.asset.id).join("-")}
            className="absolute left-0"
            style={{
              top: row.top + headerHeight,
              width: row.width,
              height: row.height,
            }}
          >
            {row.items.map((item) => renderItem(item))}
          </div>
        ))}
      </div>
    </div>
  );
}
