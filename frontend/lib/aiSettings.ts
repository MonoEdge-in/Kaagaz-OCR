// AI settings by task (spec 17 Oct 2026): the words, the rules the screens follow, and
// the one hook that asks GET /ai-settings. Tasks are named by the job, never by a model;
// no price or cost words. The server checks everything again; this only reflects it.

import { useEffect, useState } from "react";
import { ApiError, getAiSettings } from "./api";
import { errorWords } from "./errorWords";
import type {
  TaskLevel,
  AiSettings,
  AiTask,
  AiTaskSetting,
  ChatOptions,
  ReviewStatus,
  ThinkingLevel,
  UploadReader,
} from "./types";

export const TASKS: readonly AiTask[] = ["scan", "ask", "ensemble", "autofile"];

export const TASK_NAMES: Record<AiTask, string> = {
  scan: "Scanning",
  ask: "Ask",
  ensemble: "Ensemble",
  autofile: "Auto-filing",
};

/** What each setting is for, behind its "What's this?" (plain words, 25 Oct 2026). */
export const TASK_HELP: Record<AiTask, string> = {
  scan: "Who reads your uploaded scans and photos, and how carefully. More care is slower but makes fewer mistakes.",
  ask: "How much care goes into answers to your questions. More care is slower but better for tricky questions.",
  ensemble: "After a scan, lets you have the words the reader was unsure of read again by several AI readers.",
  autofile: "Puts new uploads into a folder for their type, like Invoices, so you don't have to.",
};

export const LEVEL_NAMES: Record<TaskLevel, string> = {
  none: "None",
  save: "AI for harder pages",
  low: "Low",
  high: "High",
  max: "Max",
  off: "Off",
  on: "On",
};

export const CHOICES: Record<AiTask, readonly TaskLevel[]> = {
  scan: ["none", "low", "high", "max"],
  ask: ["none", "low", "high", "max"],
  ensemble: ["off", "on"],
  autofile: ["off", "on"],
};

/** The short hint under the chosen level. */
export const HINTS: Record<AiTask, Partial<Record<TaskLevel, string>>> = {
  scan: {
    low: "A quick read for clear pages, with extra care for hard words.",
    high: "A careful read of every page.",
    max: "The most careful read your organisation allows, with a second look at unclear pages.",
  },
  ask: {
    none: "Answers only quote the exact lines found.",
    low: "A quick answer.",
    high: "A more thorough answer.",
    max: "The most thorough answer your organisation allows.",
  },
  ensemble: {
    off: "Ensemble is not offered after a scan.",
    on: "Run Ensemble is offered after a scan: unsure words are read again and the readings compared.",
  },
  autofile: {
    off: "New files stay where they are uploaded.",
    on: "New files go into folders by what they are, like invoices or land records. A folder is made when one is needed.",
  },
};

/**
 * Scanning's reader choice (#398): the scan level says who reads. None is the local reader only,
 * Save the local reader for clean printed pages and AI for the rest, Low to Max AI for every page
 * (with the care the level gives). No model names and no money words.
 */
export type ScanReader = "ai" | "save" | "local";
export const SCAN_READERS: readonly ScanReader[] = ["ai", "save", "local"];
export const READER_NAMES: Record<ScanReader, string> = {
  ai: "AI reads every page",
  save: LEVEL_NAMES.save,
  local: "Local reader only",
};
export const READER_HINTS: Record<ScanReader, string> = {
  ai: "Every page is read by AI and checked by the local reader.",
  save: "Clean printed pages are read on our server; harder pages by AI. Values from the server reader need a quick review.",
  local: "Pages are read on our server only. Handwriting and Hindi/Marathi read less well.",
};
/** AI's levels of care, under "AI reads every page". */
export const AI_LEVELS: readonly TaskLevel[] = ["low", "high", "max"];
const READER_LEVEL: Record<ScanReader, TaskLevel> = { ai: "low", save: "save", local: "none" };

export const readerOf = (level: TaskLevel): ScanReader => (level === "none" ? "local" : level === "save" ? "save" : "ai");

/** The scan level a reader is picked as: AI keeps a level of care already chosen, else Low. */
export const levelOfReader = (reader: ScanReader, current: TaskLevel): TaskLevel =>
  reader === "ai" && readerOf(current) === "ai" ? current : READER_LEVEL[reader];

/** A reader the organisation may pick now (the server leaves Save out when it is not offered). */
export const readerAllowed = (task: AiTaskSetting, reader: ScanReader): boolean => task.allowed.includes(READER_LEVEL[reader]);

/** A level as the settings screens name it: Scanning's by its reader, AI's with its care. */
export const levelName = (task: AiTask, level: TaskLevel): string =>
  task !== "scan" ? LEVEL_NAMES[level] : readerOf(level) === "ai" ? `${READER_NAMES.ai} · ${LEVEL_NAMES[level]}` : READER_NAMES[readerOf(level)];

export const NOT_ALLOWED = "Not available for your organisation.";
export const SET_BY_ADMIN = "Set by your admin.";
export const USE_ORGANISATION = "Use the organisation's setting";
export const MEMBERS_CAN_CHANGE = "Members can change this";

// Save reads like Automatic (Low), with clean printed pages kept on our server.
const RANK: Record<TaskLevel, number> = { none: 0, off: 0, save: 1, low: 1, on: 1, high: 2, max: 3 };

export const taskOf = (settings: AiSettings | null, task: AiTask): AiTaskSetting | undefined =>
  settings?.tasks.find((each) => each.task === task);

/** What a task is set to where it is used: undefined while unknown (the server decides then). */
export const effectiveOf = (settings: AiSettings | null, task: AiTask): TaskLevel | undefined => {
  const found = taskOf(settings, task);
  if (!found) return undefined;
  return found.available ? found.effective : CHOICES[task][0]; // switched off: none / off
};

// The parts of a screen that mount together (the shell's upload queue, the document page, its
// word review) share one request; one that mounts after it has answered asks again.
let asking: Promise<AiSettings> | null = null;
const askSettings = () => (asking ??= getAiSettings().finally(() => (asking = null)));

/** Asked once per screen that uses a task; null until it answers, or when it fails. */
export function useAiSettings(): AiSettings | null {
  const [settings, setSettings] = useState<AiSettings | null>(null);
  useEffect(() => {
    let cancelled = false;
    askSettings().then(
      (result) => !cancelled && setSettings(result),
      () => undefined, // unknown: nothing is capped here, the server still is
    );
    return () => {
      cancelled = true;
    };
  }, []);
  return settings;
}

// --- Where tasks are used --------------------------------------------------------------

/** A "Read with" reading's level: Automatic is Low, Careful is Max, Standard is High. */
const readerRank = (reader: UploadReader) =>
  reader.id === "dynamic" ? RANK.low : reader.title === "Careful" ? RANK.max : RANK.high;

/** "Read with" never offers more than the effective scan level; None offers nothing. */
export function capReaders(readers: readonly UploadReader[], level: TaskLevel | undefined): UploadReader[] {
  if (level === undefined) return [...readers];
  if (RANK[level] === 0) return [];
  const capped = readers.filter((reader) => readerRank(reader) <= RANK[level]);
  return capped.length > 0 ? capped : readers.slice(0, 1);
}

/** The reading "Read with" starts from: the strongest one left after the cap; null: the first. */
export function startReader(readers: readonly UploadReader[], level: TaskLevel | undefined): string | null {
  if (level === undefined) return null;
  const usable = readers.filter((reader) => reader.available);
  return usable.reduce<UploadReader | null>((best, each) => (!best || readerRank(each) > readerRank(best) ? each : best), null)?.id ?? null;
}

const THINKING_RANK: Record<ThinkingLevel, number> = { low: RANK.low, medium: 1.5, high: RANK.high };

/**
 * Ask's level picker: starts from, and never goes past, the effective ask level (Max thinks
 * High; the server picks the strongest model). None leaves no levels to pick.
 */
export function capChatOptions(options: ChatOptions, level: TaskLevel | undefined): ChatOptions {
  if (level === undefined) return options;
  if (RANK[level] === 0) return { ...options, agents: [], levels: [] }; // None: nothing to pick
  const levels = options.levels.filter((each) => THINKING_RANK[each] <= RANK[level]);
  const start = level === "low" ? "low" : "high";
  return {
    ...options,
    levels,
    default_level: levels.includes(start) ? start : (levels.at(-1) ?? options.default_level),
  };
}

/** Ensemble's after-scan controls go when the effective Ensemble is off; a run already made still shows. */
export function gateEnsemble(status: ReviewStatus, level: TaskLevel | undefined): ReviewStatus {
  return level === "off" ? { ...status, ensemble: { ...status.ensemble, allowed: false, reason: null } } : status;
}

// --- The settings screens --------------------------------------------------------------

export type OrgDraft = Partial<Record<AiTask, { level: TaskLevel; locked: boolean }>>;
export type MineDraft = Partial<Record<AiTask, TaskLevel | null>>;

export const orgDraftOf = (settings: AiSettings): OrgDraft =>
  Object.fromEntries(settings.tasks.map((each) => [each.task, { level: each.organisation ?? each.effective, locked: each.locked }]));

export const mineDraftOf = (settings: AiSettings): MineDraft =>
  Object.fromEntries(settings.tasks.map((each) => [each.task, each.mine]));

/** Only the tasks that differ from what is saved: nothing means no Save bar. */
export function changedOnly<T>(saved: Partial<Record<AiTask, T>>, draft: Partial<Record<AiTask, T>>): Partial<Record<AiTask, T>> {
  return Object.fromEntries(
    TASKS.filter((task) => task in draft && JSON.stringify(draft[task]) !== JSON.stringify(saved[task])).map((task) => [task, draft[task]]),
  );
}

/** A refused save, in calm words. */
export function aiSaveErrorText(caught: unknown): string {
  if (!(caught instanceof ApiError)) return "Could not save. Check your connection, then try again.";
  if (caught.code === "not_allowed") return "One of these choices is not available for your organisation. Choose again.";
  if (caught.code === "locked") return "Your admin has set this, so it can't be changed here.";
  return errorWords(caught, "customer"); // issue 468: never a proxy's "Something went wrong."
}
