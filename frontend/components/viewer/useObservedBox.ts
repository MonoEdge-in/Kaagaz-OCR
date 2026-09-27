"use client";

// Measures an element's content box (ResizeObserver) and, for lazy previews,
// notes once it has come within `rootMargin` of the viewport
// (IntersectionObserver). It is a callback ref, so state is set only from the
// observers' callbacks: never during render and never in an effect body.

import { useCallback, useState } from "react";

export interface ObservedBox {
  width: number;
  height: number;
}

export function useObservedBox(lazy: boolean, rootMargin = "200px") {
  const [box, setBox] = useState<ObservedBox | null>(null);
  const [seen, setSeen] = useState(false);

  const ref = useCallback(
    (element: HTMLElement | null) => {
      if (!element) return;
      const stops: (() => void)[] = [];
      const measure = (rawWidth: number, rawHeight: number) => {
        const width = Math.floor(rawWidth);
        const height = Math.floor(rawHeight);
        setBox((current) =>
          current && current.width === width && current.height === height
            ? current
            : { width, height },
        );
      };
      if (typeof ResizeObserver === "undefined") {
        measure(element.clientWidth, element.clientHeight);
      } else {
        const resize = new ResizeObserver((entries) => {
          const entry = entries[0];
          if (entry) measure(entry.contentRect.width, entry.contentRect.height);
        });
        resize.observe(element);
        stops.push(() => resize.disconnect());
      }
      if (lazy) {
        if (typeof IntersectionObserver === "undefined") {
          setSeen(true);
        } else {
          const near = new IntersectionObserver(
            (entries) => {
              if (entries.some((entry) => entry.isIntersecting)) {
                setSeen(true);
                near.disconnect();
              }
            },
            { rootMargin },
          );
          near.observe(element);
          stops.push(() => near.disconnect());
        }
      }
      return () => {
        for (const stop of stops) stop();
      };
    },
    [lazy, rootMargin],
  );

  return { ref, box, near: !lazy || seen };
}
