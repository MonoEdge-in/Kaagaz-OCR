// The operator's Models and AI spend tabs, an organisation's AI settings and the platform
// defaults (model choice, council 11 Oct 2026, MC2, MC3, MS1, MS2, SP3, SP4; features
// 25-27, council 15 Oct 2026, C10, C12): the words for a model and for money, the AI
// settings form's draft and checks, the spend periods and totals. Kept pure, so vitest
// can prove them. Dollars stay in the operator console: org admins and members never see
// money (Tushar, 15 Oct 2026).

import { PART_NOT_KNOWN, formatUsd } from "@/lib/format";
import type { AiDefaults, AiEnv, AiLevel, AiValues, OperatorModel, OperatorUsageRow, OrgInsight, UsageTotals } from "@/lib/types";
import { countText } from "./logic";

// numeric(12,2): the largest budget the server's column holds.
export const MAX_BUDGET = 9_999_999_999.99;

const perMillion = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

/** US dollars as lib/format's formatUsd says them, or "Not known" without a number. */
export function usd(value: number | null, estimated = false): string {
  return value === null || !Number.isFinite(value) ? "Not known" : formatUsd(value, estimated);
}

/** A live price: "$2.00" (per million tokens). */
export const priceText = (perM: number) => perMillion.format(perM);

/** "List price $3.00 in · $15.00 out per million tokens". */
export const listPriceText = (model: OperatorModel) =>
  `List price ${priceText(model.price_in_usd_per_m)} in · ${priceText(model.price_out_usd_per_m)} out per million tokens`;

/**
 * Tushar, 16 Oct 2026: only the REAL cost, what OpenRouter charged in the last 30 days
 * (0031), never tokens times the list price. Scanning: "$42.49 per 1,000 pages · from 312
 * pages, last 30 days"; answering: "$0.12 per 1,000 questions · from 40 questions, last
 * 30 days". Calls with no reported cost are said beside the number, never guessed; no
 * real use says the list price and "not used yet".
 */
export function realCostText(model: OperatorModel, area: Area): string {
  const [units, costed, cost, unknown, one, many] =
    area === "scan"
      ? [model.scan_pages, model.scan_pages_costed, model.scan_cost_usd, model.scan_cost_unknown_calls, "page", "pages"]
      : [model.answer_questions, model.answer_questions_costed, model.answer_cost_usd, model.answer_cost_unknown_calls, "question", "questions"];
  if (units === 0) return `${listPriceText(model)} · not used yet`;
  const notKnown = unknown > 0 ? `cost not known for ${countText(unknown, "call", "calls")}` : "";
  if (costed === 0 || cost === null) return `${listPriceText(model)} · ${notKnown || "cost not known"}, last 30 days`;
  const real = `${usd((cost / costed) * 1000)} per 1,000 ${many} · from ${countText(costed, one, many)}, last 30 days`;
  return notKnown ? `${real} · ${notKnown}` : real;
}

// ponytail: one rate set by hand (Oct 2026), so the rupees drift from the real rate; read a
// live rate if anyone ever acts on the rupee figure rather than the dollars.
export const INR_PER_USD = 88;
const rupees = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

/** The budget in rupees, roughly: "≈ ₹10,560 a month (at ₹88 per $)"; null without a budget. */
export const inrText = (dollars: number | null | undefined) =>
  typeof dollars === "number" ? `≈ ${rupees.format(dollars * INR_PER_USD)} a month (at ₹${INR_PER_USD} per $)` : null;

/** "This month: $58.10 used", from the operator insights; null when no cost is known. */
export function monthSpentText(org: OrgInsight | undefined): string | null {
  if (!org || org.month_cost_usd === null) return null;
  const part = org.month_cost_part_unknown ? ` ${PART_NOT_KNOWN}` : "";
  return `This month: ${usd(org.month_cost_usd, org.month_cost_estimated)} used${part}`;
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** A model's job in Compare models (board 8): "Scan default (High) · chat default (Medium)",
 * else what it can do: "Chat only · not used for scan". */
export function jobText(model: OperatorModel, platform: AiDefaults | null): string {
  const jobs: string[] = [];
  if (platform) {
    const { defaults, env } = platform;
    const scanDefault = defaults.scan_models.length > 0 ? defaults.scan_default : env.scan_model;
    const chatDefault = defaults.chat_models.length > 0 ? defaults.chat_default : env.chat_model;
    const scanLevel = defaults.scan_thinking ? LEVEL_WORDS[defaults.scan_thinking] : env.scan_thinking;
    if (model.id === scanDefault) jobs.push(`scan default (${scanLevel})`);
    if (model.id === chatDefault)
      jobs.push(defaults.chat_default_level ? `chat default (${LEVEL_WORDS[defaults.chat_default_level]})` : "chat default");
  }
  if (jobs.length === 0) {
    const scan = canScan(model);
    const chat = canAnswer(model);
    if (model.enabled_at === null) jobs.push("not enabled");
    else if (!model.vendor_allowed) jobs.push("never used: its maker is not allowed");
    else jobs.push(scan && chat ? "scan and chat" : scan ? "scan only · not used for chat" : chat ? "chat only · not used for scan" : "not used for scan or chat");
  }
  return capital(jobs.join(" · "));
}

export const yesNo = (value: boolean) => (value ? "Yes" : "No");

/** Scanning: eligible and verified for the reader's current prompt, as the server. */
export const canScan = (model: OperatorModel) => model.eligible && model.verified;
/** Answering: chat-eligible and verified for the current answer prompt, as the server. */
export const canAnswer = (model: OperatorModel) => model.chat_eligible && model.chat_verified;

/** Enable a model that can scan or answer, as the server. */
export const canEnable = (model: OperatorModel) => (canScan(model) || canAnswer(model)) && model.enabled_at === null;

/** What the operator can do to a model now: enable it, disable it, or nothing. */
export function modelAction(model: OperatorModel): "enable" | "disable" | null {
  if (model.enabled_at !== null) return "disable";
  return canEnable(model) ? "enable" : null;
}

/** Why a model can't be enabled, in plain words; null when it can (or is). */
export function notEnabledReason(model: OperatorModel): string | null {
  if (model.enabled_at !== null || canEnable(model)) return null;
  if (!model.eligible && !model.chat_eligible) return "Does not pass the catalogue's rules now.";
  return "Run verify-models --record for it first.";
}

/** What a model is for, in words: "Scanning and answering", "Scanning", "Answering" or "Neither yet". */
export function usesText(model: OperatorModel): string {
  const scan = canScan(model);
  const chat = canAnswer(model);
  if (scan && chat) return "Scanning and answering";
  return scan ? "Scanning" : chat ? "Answering" : "Neither yet";
}

/** S1: a model whose maker is not allowed is never used, even when enabled. */
export const VENDOR_NOT_ALLOWED = "Never used: its maker is not on the allowed list (OPENROUTER_MODEL_VENDORS).";

// --- AI settings: one organisation's, or the platform defaults (features 25-27) ----------

export const LEVELS: AiLevel[] = ["low", "medium", "high"];
export const LEVEL_WORDS: Record<AiLevel, string> = { low: "Low", medium: "Medium", high: "High" };
export const DYNAMIC_HINT = "Picks the right reading for each page.";

export type Area = "scan" | "chat";

/** The form as typed. For an organisation an empty list or a null field follows the
 * platform defaults; for the platform an empty model list uses the server's model. */
export interface AiDraft {
  scan_models: string[];
  scan_default: string | null;
  chat_models: string[];
  chat_default: string | null;
  scan_thinking: AiLevel | null;
  chat_levels: AiLevel[];
  chat_default_level: AiLevel | null;
  dynamic: boolean | null;
  ensemble: boolean; // an organisation's own; never a platform default
  budget: string; // US dollars as typed; empty: no budget (organisation only)
}

export function draftOf(values: AiValues, extra: { ensemble: boolean; ai_budget_usd: number | null } = { ensemble: false, ai_budget_usd: null }): AiDraft {
  return {
    scan_models: [...values.scan_models],
    scan_default: values.scan_default,
    chat_models: [...values.chat_models],
    chat_default: values.chat_default,
    scan_thinking: values.scan_thinking,
    chat_levels: [...(values.chat_levels ?? [])],
    chat_default_level: values.chat_default_level,
    dynamic: values.dynamic,
    ensemble: extra.ensemble,
    budget: extra.ai_budget_usd === null ? "" : String(extra.ai_budget_usd),
  };
}

/** A section of an organisation's AI settings (board 9). Ensemble and the budget have no
 * platform default: off and no budget stand for it. */
export type Section = "scan" | "chat" | "ensemble" | "budget";

/** Does a section of the draft follow the platform default ("Same as platform default")? */
export function sameAsDefault(draft: AiDraft, section: Section): boolean {
  if (section === "ensemble") return !draft.ensemble;
  if (section === "budget") return draft.budget.trim() === "";
  return follows(aiBody(draft))[section] === "all";
}

/** "Reset section": that section back to the platform default, the others as they are. */
export function resetSection(draft: AiDraft, section: Section): AiDraft {
  switch (section) {
    case "scan":
      return { ...draft, scan_models: [], scan_default: null, scan_thinking: null, dynamic: null };
    case "chat":
      return { ...draft, chat_models: [], chat_default: null, chat_levels: [], chat_default_level: null };
    case "ensemble":
      return { ...draft, ensemble: false };
    case "budget":
      return { ...draft, budget: "" };
  }
}

/** Follow the platform defaults in both areas: no own models, levels or switches, and no
 * Ensemble (it is never a default). The budget stays. */
export const followDefaults = (draft: AiDraft): AiDraft => resetSection(resetSection(resetSection(draft, "scan"), "chat"), "ensemble");

/** Tick or untick a model of an area. The first ticked becomes the area's default;
 * unticking the default moves it to the first left, or to none. */
export function toggled(draft: AiDraft, area: Area, id: string, on: boolean): AiDraft {
  const list = draft[`${area}_models` as const];
  const models = on ? [...new Set([...list, id])] : list.filter((model) => model !== id);
  const current = draft[`${area}_default` as const];
  const keep = current !== null && models.includes(current);
  return { ...draft, [`${area}_models`]: models, [`${area}_default`]: keep ? current : (models[0] ?? null) };
}

/** Tick or untick an answer level, kept Low, Medium, High; the default stays among them. */
export function toggledLevel(draft: AiDraft, level: AiLevel, on: boolean): AiDraft {
  const levels = LEVELS.filter((each) => (each === level ? on : draft.chat_levels.includes(each)));
  const current = draft.chat_default_level;
  const keep = current !== null && levels.includes(current);
  return { ...draft, chat_levels: levels, chat_default_level: keep ? current : (levels[0] ?? null) };
}

/** The budget as a number of dollars, null for none, or undefined when it is not one. */
export function parseBudget(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d{1,10}(\.\d{1,2})?$/.test(trimmed)) return undefined;
  const value = Number(trimmed);
  return value > 0 && value <= MAX_BUDGET ? value : undefined;
}

/** Where a problem is shown: next to its field. "form" is the form's own line. */
export type AiField = "scan_models" | "scan_default" | "chat_models" | "chat_default" | "chat_levels" | "budget" | "form";
export type AiErrors = Partial<Record<AiField, string>>;

/** The form's own checks, before a code is asked for or anything is sent. */
export function aiErrors(draft: AiDraft, platform: boolean): AiErrors {
  const errors: AiErrors = {};
  if (draft.scan_models.length > 0 && (draft.scan_default === null || !draft.scan_models.includes(draft.scan_default)))
    errors.scan_default = "Choose the default scanning model from the ticked models.";
  if (draft.chat_models.length > 0 && (draft.chat_default === null || !draft.chat_models.includes(draft.chat_default)))
    errors.chat_default = "Choose the default answering model from the ticked models.";
  if (platform && draft.chat_levels.length === 0) errors.chat_levels = "Tick at least one thinking level.";
  else if (
    draft.chat_levels.length > 0 &&
    (draft.chat_default_level === null || !draft.chat_levels.includes(draft.chat_default_level))
  )
    errors.chat_levels = "Choose the default level from the ticked levels.";
  if (!platform && parseBudget(draft.budget) === undefined)
    errors.budget = "Enter dollars above 0, with at most 2 decimals, or leave it empty.";
  return errors;
}

/** What is saved; call it only when aiErrors finds none. Empty lists send no default; no
 * levels send no default level (an organisation then follows the platform's). */
export function aiBody(draft: AiDraft): AiValues {
  const levels = draft.chat_levels.length > 0;
  return {
    scan_models: draft.scan_models,
    scan_default: draft.scan_models.length > 0 ? draft.scan_default : null,
    chat_models: draft.chat_models,
    chat_default: draft.chat_models.length > 0 ? draft.chat_default : null,
    scan_thinking: draft.scan_thinking,
    chat_levels: levels ? draft.chat_levels : null,
    chat_default_level: levels ? draft.chat_default_level : null,
    dynamic: draft.dynamic,
  };
}

/** The models a list offers: every enabled one that can do that work, and any already in it. */
export function offered(catalogue: OperatorModel[], area: Area, saved: string[]): OperatorModel[] {
  const can = area === "scan" ? canScan : canAnswer;
  return catalogue.filter((model) => (model.enabled_at !== null && can(model)) || saved.includes(model.id));
}

/** Does an organisation's own value follow the platform defaults? Per area: scanning is
 * its models, scan level and Dynamic; answering its models and levels. */
export function follows(own: AiValues): { scan: "all" | "some" | "none"; chat: "all" | "some" | "none" } {
  const part = (inherited: boolean[]) =>
    inherited.every(Boolean) ? "all" : inherited.some(Boolean) ? "some" : "none";
  return {
    scan: part([own.scan_models.length === 0, own.scan_thinking === null, own.dynamic === null]),
    chat: part([own.chat_models.length === 0, own.chat_levels === null]),
  };
}

/** "Scanning: platform defaults · Answering: own settings" (C12). */
export function statusText(own: AiValues): string {
  const words = { all: "platform defaults", some: "partly own settings", none: "own settings" };
  const area = follows(own);
  return `Scanning: ${words[area.scan]} · Answering: ${words[area.chat]}`;
}

/** Model names by id; an id the catalogue no longer has is shown as itself. */
export function namesOf(ids: string[], catalogue: OperatorModel[], first: string | null = null): string {
  const named = (id: string) => catalogue.find((model) => model.id === id)?.name ?? id;
  const ordered = first !== null && ids.includes(first) ? [first, ...ids.filter((id) => id !== first)] : ids;
  return ordered.map((id, index) => (index === 0 && first !== null ? `${named(id)} (default)` : named(id))).join(", ");
}

/** What an organisation that follows the platform gets, in words, for "From platform
 * defaults: …". An empty platform list is the server's own model. */
export function inheritedText(
  field: "scan_models" | "chat_models" | "scan_thinking" | "chat_levels" | "dynamic",
  defaults: AiValues,
  env: AiEnv,
  catalogue: OperatorModel[],
): string {
  switch (field) {
    case "scan_models":
      return defaults.scan_models.length > 0 ? namesOf(defaults.scan_models, catalogue, defaults.scan_default) : `the server's model (${env.scan_model})`;
    case "chat_models":
      return defaults.chat_models.length > 0 ? namesOf(defaults.chat_models, catalogue, defaults.chat_default) : `the server's model (${env.chat_model})`;
    case "scan_thinking":
      return defaults.scan_thinking !== null ? LEVEL_WORDS[defaults.scan_thinking] : `the server's level (${env.scan_thinking})`;
    case "chat_levels":
      return levelText(defaults);
    case "dynamic":
      return defaults.dynamic ? "On" : "Off";
  }
}

/** "High, Medium; default Medium". */
function levelText(values: Pick<AiValues, "chat_levels" | "chat_default_level">): string {
  const words = (values.chat_levels ?? []).map((level) => LEVEL_WORDS[level]).join(", ");
  return values.chat_default_level ? `${words}; default ${LEVEL_WORDS[values.chat_default_level]}` : words;
}

/** Board 9: "People at Sharma Traders will see: Two (default), One; levels High, Medium;
 * default Medium", from the draft, or the platform defaults where it follows them. */
export function pickerText(orgName: string, draft: AiDraft, defaults: AiValues, env: AiEnv, catalogue: OperatorModel[]): string {
  const own = aiBody(draft);
  const models = own.chat_models.length > 0 ? namesOf(own.chat_models, catalogue, own.chat_default) : inheritedText("chat_models", defaults, env, catalogue);
  const levels = own.chat_levels !== null ? levelText(own) : inheritedText("chat_levels", defaults, env, catalogue);
  return `People at ${orgName} will see: ${models}; levels ${levels}`;
}

// The platform defaults' areas, in the order the save's summary says them; `now` says an
// organisation's value, for its save bar.
const DEFAULT_AREAS: {
  area: keyof AiDefaults["orgs_inheriting"];
  label: string;
  changed(a: AiValues, b: AiValues): boolean;
  now(v: AiValues, catalogue: OperatorModel[]): string;
}[] = [
  {
    area: "scan_models",
    label: "scanning models",
    changed: (a, b) => !same(a.scan_models, b.scan_models) || a.scan_default !== b.scan_default,
    now: (v, catalogue) => (v.scan_models.length > 0 ? namesOf(v.scan_models, catalogue, v.scan_default) : "platform defaults"),
  },
  {
    area: "scan_thinking",
    label: "scanning level",
    changed: (a, b) => a.scan_thinking !== b.scan_thinking,
    now: (v) => (v.scan_thinking ? LEVEL_WORDS[v.scan_thinking] : "platform default"),
  },
  {
    area: "dynamic",
    label: "Dynamic",
    changed: (a, b) => a.dynamic !== b.dynamic,
    now: (v) => (v.dynamic === null ? "platform default" : v.dynamic ? "On" : "Off"),
  },
  {
    area: "chat_models",
    label: "answering models",
    changed: (a, b) => !same(a.chat_models, b.chat_models) || a.chat_default !== b.chat_default,
    now: (v, catalogue) => (v.chat_models.length > 0 ? namesOf(v.chat_models, catalogue, v.chat_default) : "platform defaults"),
  },
  {
    area: "chat_levels",
    label: "answering levels",
    changed: (a, b) => !same(a.chat_levels ?? [], b.chat_levels ?? []) || a.chat_default_level !== b.chat_default_level,
    now: (v) => (v.chat_levels !== null ? levelText(v) : "platform defaults"),
  },
];

const budgetWords = (text: string) => {
  const value = parseBudget(text);
  return value === null ? "none" : value === undefined ? text.trim() : usd(value);
};

/** The save bar's list (board 9): what saving the draft changes, in words; empty: nothing. */
export function changesText(saved: AiDraft, draft: AiDraft, catalogue: OperatorModel[]): string[] {
  const [before, after] = [aiBody(saved), aiBody(draft)];
  const changes = DEFAULT_AREAS.filter((each) => each.changed(before, after)).map(
    (each) => `${capital(each.label)}: ${each.now(after, catalogue)}`,
  );
  if (saved.ensemble !== draft.ensemble) changes.push(`Ensemble: ${draft.ensemble ? "on" : "off"}`);
  const [was, now] = [budgetWords(saved.budget), budgetWords(draft.budget)];
  if (was !== now) changes.push(`Budget: ${was} to ${now}`);
  return changes;
}

const same = (a: string[], b: string[]) => [...a].sort().join("\n") === [...b].sort().join("\n");

export const KEEP_OWN = "Organisations with their own settings keep them.";

/** Before a platform defaults save (C12), as a short list (Tushar, 16 Oct 2026): "This save
 * changes, for 1 organisation still on platform defaults:" and the areas it changes; when
 * the areas reach different numbers, each says its own. "Nothing changes." (no areas)
 * when it changes no area. */
export function defaultsImpact(
  saved: AiValues,
  next: AiValues,
  counts: AiDefaults["orgs_inheriting"],
): { lead: string; areas: string[] } {
  const changed = DEFAULT_AREAS.filter((each) => each.changed(saved, next));
  if (changed.length === 0) return { lead: "Nothing changes.", areas: [] };
  const orgs = (count: number) => countText(count, "organisation", "organisations");
  const reached = [...new Set(changed.map((each) => counts[each.area]))];
  if (reached.length === 1)
    return {
      lead: `This save changes, for ${orgs(reached[0] ?? 0)} still on platform defaults:`,
      areas: changed.map((each) => capital(each.label)),
    };
  return {
    lead: "This save changes, for the organisations still on platform defaults:",
    areas: changed.map((each) => `${capital(each.label)}: ${orgs(counts[each.area])}`),
  };
}

/** A refusal of a save, next to the field it is about (C12): the server's code, its field. */
export function refusalField(code: string): AiField {
  switch (code) {
    case "not_verified":
    case "needs_two_models_scan":
      return "scan_models";
    case "chat_not_verified":
    case "needs_two_models_chat":
      return "chat_models";
    default: // unavailable, default_not_allowed, level_not_supported: either area
      return "form";
  }
}

// --- AI spend ---------------------------------------------------------------------------

export const PERIODS = [
  { id: "month", label: "This month" },
  { id: "last-month", label: "Last month" },
  { id: "7d", label: "Last 7 days" },
  { id: "30d", label: "Last 30 days" },
] as const;
export type PeriodId = (typeof PERIODS)[number]["id"];

const day = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

/** The period's first and last day (both included), on this browser's calendar.
 * ponytail: the browser's dates, not the server's TIMEZONE; the same in India. */
export function periodRange(id: PeriodId, now: Date): { from: string; to: string } {
  const y = now.getFullYear();
  const m = now.getMonth();
  const d = now.getDate();
  switch (id) {
    case "month":
      return { from: day(new Date(y, m, 1)), to: day(now) };
    case "last-month":
      return { from: day(new Date(y, m - 1, 1)), to: day(new Date(y, m, 0)) };
    case "7d":
      return { from: day(new Date(y, m, d - 6)), to: day(now) };
    case "30d":
      return { from: day(new Date(y, m, d - 29)), to: day(now) };
  }
}

/** Every row added up: the cost is not known when no row's is, part not known when some
 * row's is not, and estimated when any row's is. */
export function spendTotals(rows: OperatorUsageRow[]): UsageTotals {
  const known = rows.filter((row) => row.cost_usd !== null);
  return {
    calls: rows.reduce((sum, row) => sum + row.calls, 0),
    cached_calls: rows.reduce((sum, row) => sum + row.cached_calls, 0),
    input_tokens: rows.reduce((sum, row) => sum + row.input_tokens, 0),
    output_tokens: rows.reduce((sum, row) => sum + row.output_tokens, 0),
    reasoning_tokens: rows.reduce((sum, row) => sum + row.reasoning_tokens, 0),
    cost_usd: known.length === 0 ? null : known.reduce((sum, row) => sum + (row.cost_usd ?? 0), 0),
    cost_estimated: rows.some((row) => row.cost_estimated),
    cost_part_unknown: rows.some((row) => row.cost_usd === null || row.cost_part_unknown),
  };
}
