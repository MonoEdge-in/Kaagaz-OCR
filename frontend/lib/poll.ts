// Asking the server again while something is still changing (a document being
// checked or read), shared by the documents list and the viewer; the hook is
// lib/usePoll.ts. Unit tested under fake timers (tests/poll.test.ts).
//
// - One request at a time. A refresh asked for while one is out runs once after it.
// - Nothing is sent while the tab is hidden. When it is shown again, or the
//   browser is back online, a planned poll is sent at once.
// - It waits 3 s, then 6, 12 and 15 s while nothing changes, and goes back to
//   3 s when something does (the signature: status, status_message, progress).
// - A failed request is tried again on the same steps: one failure never stops it.

export const POLL_STEPS_MS = [3000, 6000, 12000, 15000] as const;

export interface PollTask<T> {
  load(): Promise<T>;
  onResult(result: T): void;
  onFailure(caught: unknown): void;
  /** Whether to ask again after this answer. */
  again(result: T): boolean;
  /** What counts as a change: a new signature goes back to the first step. */
  signature(result: T): string;
}

export interface Poller {
  /** Ask now, or right after the request that is out, starting again from 3 s. */
  refresh(): void;
  /** Ask after the first step, unless a request is already out or planned. */
  later(): void;
  /** The tab is shown again, or the browser is back online: send a planned poll now. */
  wake(): void;
  /** Forget the plan, and the answer of any request still out. */
  stop(): void;
}

/** task() is asked for at each request, so each request uses the screen's latest task. */
export function createPoller<T>(task: () => PollTask<T>, hidden: () => boolean): Poller {
  const lastStep = POLL_STEPS_MS.length - 1;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let out = false; // a request is out
  let queued = false; // asked for again while it was
  let due = false; // a poll came due while the tab was hidden
  let step = 0;
  let signature: string | null = null;
  let generation = 0; // bumped by stop(), so an older answer is ignored

  function cancel() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  function plan() {
    cancel();
    timer = setTimeout(() => {
      timer = null;
      run();
    }, POLL_STEPS_MS[step]);
  }

  function run() {
    if (out) {
      queued = true;
      return;
    }
    if (hidden()) {
      due = true;
      return;
    }
    due = false;
    out = true;
    const mine = generation;
    const current = task();
    let more = true;
    current
      .load()
      .then(
        (result) => {
          if (mine !== generation) return;
          const next = current.signature(result);
          step = next === signature ? Math.min(step + 1, lastStep) : 0;
          signature = next;
          more = current.again(result);
          current.onResult(result);
        },
        (caught: unknown) => {
          if (mine !== generation) return;
          step = Math.min(step + 1, lastStep);
          current.onFailure(caught);
        },
      )
      .finally(() => {
        out = false;
        if (queued) {
          queued = false;
          run();
        } else if (mine === generation && more) plan();
      });
  }

  return {
    refresh() {
      cancel();
      step = 0;
      signature = null;
      run();
    },
    later() {
      if (out || timer !== null || due) return;
      step = 0;
      signature = null;
      plan();
    },
    wake() {
      if (!due && timer === null) return;
      cancel();
      run();
    },
    stop() {
      generation += 1;
      cancel();
      queued = false;
      due = false;
    },
  };
}

interface Progressing {
  id?: string;
  status: string;
  status_message: string;
  progress?: unknown;
  newer_version?: { status: string; status_message: string } | null;
}

/** What a poll watches for a change: each document's status, status_message and progress. */
export function progressSignature<T extends Progressing>(items: readonly T[]): string {
  return JSON.stringify(
    items.map((item) => [
      item.id ?? null,
      item.status,
      item.status_message,
      item.progress ?? null,
      item.newer_version ? [item.newer_version.status, item.newer_version.status_message] : null,
    ]),
  );
}
