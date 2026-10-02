"use client";

// The poller (lib/poll.ts) for one screen: each request uses the latest task
// the screen rendered, nothing is sent while the tab is hidden, and showing
// the tab again or coming back online sends a planned poll at once.

import { useEffect, useMemo, useRef } from "react";
import { createPoller } from "./poll";
import type { PollTask, Poller } from "./poll";

export function usePoll<T>(task: PollTask<T>): Poller {
  const latest = useRef(task);
  const running = useRef<Poller | null>(null);
  useEffect(() => {
    latest.current = task;
  });
  useEffect(() => {
    const poller = createPoller(
      () => latest.current,
      () => document.visibilityState === "hidden",
    );
    running.current = poller;
    const onShown = () => {
      if (document.visibilityState === "visible") poller.wake();
    };
    document.addEventListener("visibilitychange", onShown);
    window.addEventListener("online", poller.wake);
    return () => {
      document.removeEventListener("visibilitychange", onShown);
      window.removeEventListener("online", poller.wake);
      poller.stop();
      running.current = null;
    };
  }, []);
  // Stable, so a screen can name it in an effect's dependencies.
  return useMemo(
    () => ({
      refresh: () => running.current?.refresh(),
      later: () => running.current?.later(),
      wake: () => running.current?.wake(),
      stop: () => running.current?.stop(),
    }),
    [],
  );
}
