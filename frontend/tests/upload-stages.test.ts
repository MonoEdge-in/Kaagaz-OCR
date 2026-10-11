// The upload bar tells the truth (PRD 5 U10). Reported: it jumps from about 20%
// straight to 100%. Reproduced here as the browser does it with throttling on a
// 20 MB PDF: XHR reports bytes in coarse steps (20%, then 100%), then the server
// still saves the file before it replies, then the file is read. The row moves
// through Sending, Saving and Reading, never jumps more than 15 points, and never
// shows 100% until the document is ready (components/documents/uploadStages.ts).

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MAX_STEP_PERCENT,
  jobFraction,
  nextShown,
  readFraction,
  stageOf,
  stageText,
  valueText,
} from "@/components/documents/uploadStages";
import type { Shown, StageTimes } from "@/components/documents/uploadStages";
import type { UploadState } from "@/components/documents/logic";
import { uploadDocument } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";

const MB = 1024 * 1024;
const SIZE = 20 * MB;
const READ_SECONDS = 60;

const doc = (more: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id: "d1",
  display_name: "Survey.pdf",
  detected_type: "pdf",
  size_bytes: SIZE,
  status: "clean",
  failure_code: null,
  status_message: "Reading page 1 of 8…",
  page_count: 8,
  created_at: "2026-10-09T10:00:00Z",
  uploaded_by_name: null,
  folder_id: null,
  tags: [],
  deleted_at: null,
  version_no: 1,
  family_id: "f1",
  can_delete: true,
  attempts: 1,
  max_attempts: 5,
  newer_version: null,
  progress: { step: "reading", done: 0, total: 8 },
  time_left: null,
  doc_type: null,
  languages: [],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
  ...more,
});

/** Every percent a row shows over a run of (state, times, now) steps, one per 500 ms tick. */
function run(steps: [UploadState, StageTimes, number][]): number[] {
  let shown: Shown | null = null;
  const out: number[] = [];
  for (const [state, times, now] of steps) {
    if (state.kind === "done") {
      out.push(100); // the bar is gone: the row says Ready
      continue;
    }
    shown = nextShown(shown, jobFraction(state, SIZE, READ_SECONDS, times, now), now);
    out.push(shown.percent);
  }
  return out;
}

const times = (startedAt: number | null, sentAt: number | null = null, repliedAt: number | null = null) => ({
  startedAt,
  sentAt,
  repliedAt,
});

/** A throttled 20 MB PDF: 20% after 2 s, 100% at 10 s, saved by 14 s, then 8 pages read. */
function throttled(): [UploadState, StageTimes, number][] {
  const steps: [UploadState, StageTimes, number][] = [];
  let now = 0;
  const tick = (state: UploadState, at: StageTimes) => {
    now += 500;
    steps.push([state, at, now]);
  };
  for (let i = 0; i < 4; i += 1) tick({ kind: "sending", fraction: 0.2 }, times(0));
  for (let i = 0; i < 12; i += 1) tick({ kind: "sending", fraction: 0.2 }, times(0));
  tick({ kind: "sending", fraction: 1 }, times(0));
  for (let i = 0; i < 8; i += 1) tick({ kind: "saving" }, times(0, 10_000));
  for (let page = 0; page <= 8; page += 1) {
    const document = doc({ progress: { step: "reading", done: page, total: 8 } });
    for (let i = 0; i < 4; i += 1) tick({ kind: "reading", documentId: "d1", document }, times(0, 10_000, 14_000));
  }
  tick({ kind: "done", documentId: "d1", document: doc({ status: "ready", progress: null }) }, times(0, 10_000, 14_000));
  return steps;
}

describe("the upload bar (U10)", () => {
  it("never jumps more than 15 points, though the bytes jump from 20% to 100%", () => {
    const percents = run(throttled()).slice(0, -1);
    for (let i = 1; i < percents.length; i += 1) {
      expect(percents[i]! - percents[i - 1]!).toBeLessThanOrEqual(MAX_STEP_PERCENT);
    }
  });

  it("never moves backwards", () => {
    const percents = run(throttled());
    for (let i = 1; i < percents.length; i += 1) expect(percents[i]).toBeGreaterThanOrEqual(percents[i - 1]!);
  });

  it("never shows 100% while the file is sending, saving or being read", () => {
    const steps = throttled();
    const percents = run(steps);
    steps.forEach(([state], i) => {
      if (state.kind !== "done") expect(percents[i]).toBeLessThan(100);
    });
    expect(percents.at(-1)).toBe(100);
  });

  it("leaves room for reading: the last byte is far from 100%", () => {
    const sent = jobFraction({ kind: "sending", fraction: 1 }, SIZE, READ_SECONDS, times(0), 10_000);
    expect(sent).toBeLessThan(0.5);
    const saved = jobFraction({ kind: "saving" }, SIZE, READ_SECONDS, times(0, 10_000), 60_000);
    expect(saved).toBeLessThan(0.5);
  });

  it("weights sending by the measured speed: a fast upload is a small part of the bar", () => {
    const fast = jobFraction({ kind: "sending", fraction: 0.5 }, SIZE, READ_SECONDS, times(0), 500);
    const slow = jobFraction({ kind: "sending", fraction: 0.5 }, SIZE, READ_SECONDS, times(0), 30_000);
    expect(fast).toBeLessThan(slow);
  });

  it("reads the reading fraction from the server: done_fraction first, else pages read", () => {
    expect(readFraction(doc({ progress: { step: "reading", done: 2, total: 8 } }))).toBe(0.25);
    const timeLeft = {
      seconds: 30,
      since_move_seconds: 0,
      slow_after_seconds: 100,
      items_left: 4,
      item_seconds: 7,
      after_seconds: 0,
      confidence: "high" as const,
      elapsed_seconds: 30,
      done_fraction: 0.5,
    };
    expect(readFraction(doc({ time_left: timeLeft }))).toBe(0.5);
    expect(readFraction(doc({ status: "received", progress: null }))).toBe(0);
  });

  it("takes at most one step a tick", () => {
    const first = nextShown(null, 0.9, 500);
    expect(first.percent).toBe(MAX_STEP_PERCENT);
    expect(nextShown(first, 0.9, 500)).toBe(first);
    expect(nextShown(first, 0.9, 1000).percent).toBe(2 * MAX_STEP_PERCENT);
    expect(nextShown({ percent: 98, at: 0 }, 1, 500).percent).toBe(99);
  });
});

describe("the stages, in words", () => {
  it("names each stage, and gives the bar its words with the percent", () => {
    expect(stageOf({ kind: "waiting" })).toBeNull();
    expect(stageOf({ kind: "sending", fraction: 0.3 })).toBe("sending");
    expect(stageOf({ kind: "saving" })).toBe("saving");
    expect(stageOf({ kind: "reading", documentId: "d1", document: doc() })).toBe("reading");
    expect(stageOf({ kind: "done", documentId: "d1" })).toBeNull();
    expect(stageText("sending")).toBe("Sending");
    expect(stageText("saving")).toBe("Saving…");
    expect(stageText("reading")).toBe("Reading");
    expect(valueText("sending", 42)).toBe("Sending, 42%");
    expect(valueText("saving", 30)).toBe("Saving");
    expect(valueText("reading", 63)).toBe("Reading, 63%");
  });
});

describe("sending (lib/api.ts)", () => {
  class FakeXhr {
    static sent: FakeXhr[] = [];
    status = 0;
    responseText = "";
    withCredentials = false;
    body: unknown = null;
    upload: {
      onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null;
      onload: (() => void) | null;
    } = { onprogress: null, onload: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    open() {}
    setRequestHeader() {}
    send(body: unknown) {
      this.body = body;
      FakeXhr.sent.push(this);
    }
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    FakeXhr.sent = [];
  });

  it("says when the last byte has gone (Saving starts), and the bytes never go backwards", () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const fractions: number[] = [];
    let sent = 0;
    const file = new File(["%PDF-1.7"], "survey.pdf");
    void uploadDocument(file, "u1", (f) => fractions.push(f), undefined, undefined, "b1", () => (sent += 1));
    const xhr = FakeXhr.sent[0]!;
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 2, total: 10 });
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 10 });
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 10, total: 10 });
    expect(fractions).toEqual([0.2, 1]);
    expect(sent).toBe(0);
    xhr.upload.onload?.();
    expect(sent).toBe(1);
    expect((xhr.body as FormData).get("batch_id")).toBe("b1");
  });
});
