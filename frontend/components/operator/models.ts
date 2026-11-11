// The operator's Models and AI spend tabs, an organisation's AI settings and the platform
// defaults (model choice, council 11 Oct 2026, MC2, MC3, MS1, MS2, SP3, SP4; features
// 25-27, council 15 Oct 2026, C10, C12): the words for a model and for money, the AI
// settings form's draft and checks, the spend periods and totals. Kept pure, so vitest
// can prove them. Dollars stay in the operator console: org admins and members never see
// money (Tushar, 15 Oct 2026).

import { PART_NOT_KNOWN, formatDateTime, formatNumber, formatUsd } from "@/lib/format";
import type {
  AiDefaults,
  AiEnv,
  AiLevel,
  AiValues,
  OperatorModel,
  OperatorUsageRow,
  OrgAiReply,
  OrgInsight,
  AccuracyCheck,
  MistralModel,
  MistralModelId,
  Resolution,
  ScanCost,
  ScanCostAsk,
  ScanLevel,
  ScanReader,
  UsageTotals,
} from "@/lib/types";
import { ApiError } from "@/lib/api";
import { CEILING_RANGE, DAILY_PAGES_RANGE, REFUSALS, TOKENS_RANGE, countText, operatorErrorMessage } from "./logic";

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
 * (0033), never tokens times the list price. Scanning: "$42.49 per 1,000 pages · from 312
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

/** Tushar, 18 Oct 2026: a model that lists no thinking levels is always called with none;
 * any level set for it means its one mode. Super admin only (it names the model). */
export const NO_LEVELS = "No thinking levels";
export const hasNoLevels = (model: Pick<OperatorModel, "efforts">) => (model.efforts ?? []).length === 0;

// --- AI settings: one organisation's, or the platform defaults (features 25-27) ----------

export const LEVELS: AiLevel[] = ["low", "medium", "high"];
// Minimal is for scanning only (#463, Tushar, 31 Oct 2026): the answering lists keep LEVELS.
export const SCAN_LEVELS: ScanLevel[] = ["minimal", ...LEVELS];
export const LEVEL_WORDS: Record<ScanLevel, string> = { minimal: "Minimal", low: "Low", medium: "Medium", high: "High" };
export const RESOLUTIONS: Resolution[] = ["high", "low"];
export const RESOLUTION_WORDS: Record<Resolution, string> = { high: "High", low: "Low" };
export const RESOLUTION_HINT = "Low reads fewer picture details: cheaper, but small handwriting gets harder.";
export const CAREFUL_THINKING_HINT =
  "How hard the model thinks when a hard page is read again, by Cost saving or Read this page again. Higher costs more.";
export const CAREFUL_RESOLUTION_HINT = "The picture detail a page is read again with. Low is cheaper, but small handwriting gets harder.";
// Cost saving (Tushar, 16 Oct 2026): the super admin's name for Dynamic (its id and the
// `dynamic` field stay). Organisations never see it: their "Read with" calls it "Automatic".
export const COST_SAVING = "Cost saving";
export const DYNAMIC_HINT = "Reads clear pages quickly and spends extra effort only on hard-to-read words.";
// Any number of scanning models (Tushar, 16 Oct 2026): Ensemble asked with fewer than two
// in effect is saved off, and the save says so calmly (0037).
export const ENSEMBLE_OFF = "Ensemble is off: it needs a second scanning model.";

// Who reads the pages (#473, Tushar, 2 Nov 2026): Gemini through OpenRouter or Mistral OCR; or
// (#488, council 4 Nov 2026) ultra low cost, Gemini read as briefly as possible under a ceiling.
export const READERS: ScanReader[] = ["openrouter", "mistral", "ultra_low_cost"];
// The server reads with Gemini when the platform names no reader.
export const READER_WORDS: Record<ScanReader, string> = {
  openrouter: "Gemini",
  mistral: "Mistral OCR",
  ultra_low_cost: "Ultra low cost (Gemini)",
};
// Inside other words ("Platform default (…)"): no brackets in brackets.
const READER_SHORT: Record<ScanReader, string> = { ...READER_WORDS, ultra_low_cost: "Ultra low cost" };
export const ULC_HINT =
  "Each page is read as briefly as possible and never costs more than the most you set. Hard handwriting may read less well.";
/** In place of the fields ultra low cost sets itself: the draft keeps their values. */
export const ULC_HIDDEN = "Thinking, the output limit and pages read again are set by ultra low cost. They come back if you switch.";
export const ENSEMBLE_NOT_WITH_ULC = "Ensemble isn't used while ultra low cost reads the pages.";
// Its model (#488, 4 Nov 2026): the default scan model reads, so choosing one sets (and ticks) it.
export const ULC_MODEL_LABEL = "Model for ultra low cost";
export const ULC_NO_MODEL = "No model can be used yet. Ask for the model list to be refreshed (refresh-models).";
/** An option of the ultra low cost model: its name, and why it can't be chosen when it can't. */
export const ulcModelText = (model: OperatorModel) => (model.ulc_ready ? model.name : `${model.name} (not available: ${model.ulc_why})`);
/** Ultra low cost reads with ``id``: it is ticked and made the default scan model. */
export const ulcModelPicked = (draft: AiDraft, id: string): AiDraft => ({ ...toggled(draft, "scan", id, true), scan_default: id });
// The ceiling (#488): the most one read of a page may cost, ₹0.20 to ₹0.55 (0073), sent in paise.
export const CEILING_LABEL = "Most a page may cost";
export const CEILING_HINT =
  "From ₹0.20 to ₹0.55. A page that would cost more is read by Kaagaz's own reader instead and marked for a check.";
const [MIN_CEILING_PAISE, MAX_CEILING_PAISE] = [20, 55]; // the server's ULC_CEILING_PAISE
export const MISTRAL_NOT_SET_UP = "Mistral OCR (not set up on this server)";
export const MISTRAL_MISSING: Record<"key" | "zdr", string> = {
  key: "Mistral can't be chosen yet: the server has no Mistral key.",
  zdr: "Mistral can't be chosen yet: we haven't confirmed that Mistral keeps no copy of our pages.",
};
export const MISTRAL_WAITING =
  "Saved as Mistral, but Mistral isn't set up on this server, so this organisation's pages are waiting. Choose Gemini to read them now.";
export const NO_BLOCKS_HINT =
  "This model doesn't say where each line is on the page, so answers can't point to the spot and every amount waits for a person to check it.";
export const ENSEMBLE_NOT_WITH_MISTRAL = "Ensemble isn't used while Mistral reads the pages.";
/** In place of the Gemini fields while Mistral reads: the draft keeps their values. */
export const GEMINI_HIDDEN = "Gemini's reading settings are hidden while Mistral reads the pages. They come back if you switch to Gemini.";

/** A Mistral model's name, from the server's list; an id it no longer has is shown as itself. */
export const mistralName = (id: MistralModelId, models: MistralModel[]) => models.find((model) => model.id === id)?.name ?? id;

/** "Mistral OCR 4.1: about ₹0.35 a page, shows where each line is". */
export const mistralModelText = (model: MistralModel) =>
  `${model.name}: about ${rupeeText(model.usd_per_page)} a page, ${
    model.has_blocks ? "shows where each line is" : "no page spots, every amount checked by a person"
  }`;

/** The reader a scan really uses: the draft's, else the one it follows (the platform's for an
 * organisation), else the server's. Everything about the reader follows it. */
export const readerOf = (draft: Pick<AiDraft, "scan_reader">, defaults: AiValues, env: AiEnv, platform: boolean): ScanReader =>
  draft.scan_reader ?? (platform ? null : defaults.scan_reader) ?? env.scan_reader;
/** The Mistral model a scan really uses, the same way. */
export const mistralModelOf = (draft: Pick<AiDraft, "mistral_model">, defaults: AiValues, env: AiEnv, platform: boolean): MistralModelId =>
  draft.mistral_model ?? (platform ? null : defaults.mistral_model) ?? env.mistral_model;

/** Pick a reader ("" follows); the Mistral model is let go of unless Mistral then reads, and the
 * ceiling unless ultra low cost then reads (#488). */
export function pickedReader(draft: AiDraft, value: string, defaults: AiValues, env: AiEnv, platform: boolean): AiDraft {
  const next = { ...draft, scan_reader: (value || null) as ScanReader | null };
  const reader = readerOf(next, defaults, env, platform);
  return {
    ...next,
    mistral_model: reader === "mistral" ? next.mistral_model : null,
    ulc_ceiling: reader === "ultra_low_cost" ? next.ulc_ceiling : "",
  };
}

/** The banner's lines while Mistral is stopped; null while it reads. The count is every
 * organisation's. Mistral chosen on a server where it isn't set up says that, and why. */
export function mistralStoppedText(env: Pick<AiEnv, "mistral_status" | "mistral_ready" | "mistral_missing">): string[] | null {
  const status = env.mistral_status;
  if (!status.stopped) return null;
  const waiting = `${countText(status.documents_waiting, "document", "documents")} ${status.documents_waiting === 1 ? "is" : "are"} waiting across all organisations`;
  if (status.code === "mistral_setup" || !env.mistral_ready) {
    const lines = [`Mistral isn't set up on this server, so ${waiting}. They'll be read once it's set up, or switch these organisations to Gemini.`];
    if (env.mistral_missing) lines.push(MISTRAL_MISSING[env.mistral_missing]);
    return lines;
  }
  const stopped = status.since ? `Mistral hasn't read pages since ${formatDateTime(status.since)}` : "Mistral stopped reading pages";
  const why = status.code === "mistral_key" ? "out of credit or key refused" : "not answering";
  const lines = [`${stopped} (${why}). ${waiting}. They're read automatically once Mistral works again.`];
  if (status.code === "mistral_key") lines.push("In Mistral's console, check the Mistral key and credit.");
  return lines;
}

/** What a saved organisation's AI settings say, with the Ensemble note when the server turned it off. */
export const savedText = (orgName: string, ensembleOff: boolean) =>
  `Saved the AI settings of ${orgName}.${ensembleOff ? ` ${ENSEMBLE_OFF}` : ""}`;

export type Area = "scan" | "chat";

/** The form as typed. For an organisation an empty list or a null field follows the
 * platform defaults; for the platform an empty model list uses the server's model. */
export interface AiDraft {
  scan_models: string[];
  scan_default: string | null;
  chat_models: string[];
  chat_default: string | null;
  scan_thinking: ScanLevel | null;
  scan_resolution: Resolution | null;
  careful_thinking: ScanLevel | null;
  careful_resolution: Resolution | null;
  page_max_output_tokens: string; // as typed; empty follows
  daily_page_limit: string; // #478: as typed; empty follows (the platform's: no limit)
  chat_levels: AiLevel[];
  chat_default_level: AiLevel | null;
  dynamic: boolean | null;
  scan_reader: ScanReader | null; // #473
  mistral_model: MistralModelId | null;
  ulc_ceiling: string; // #488: rupees as typed ("0.30"); empty follows
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
    scan_resolution: values.scan_resolution,
    careful_thinking: values.careful_thinking,
    careful_resolution: values.careful_resolution,
    page_max_output_tokens: values.page_max_output_tokens === null ? "" : String(values.page_max_output_tokens),
    daily_page_limit: values.daily_page_limit === null ? "" : String(values.daily_page_limit),
    chat_levels: [...(values.chat_levels ?? [])],
    chat_default_level: values.chat_default_level,
    dynamic: values.dynamic,
    scan_reader: values.scan_reader,
    mistral_model: values.mistral_model,
    ulc_ceiling: values.ulc_ceiling_paise === null ? "" : (values.ulc_ceiling_paise / 100).toFixed(2),
    ensemble: extra.ensemble,
    budget: extra.ai_budget_usd === null ? "" : String(extra.ai_budget_usd),
  };
}

/** A section of an organisation's AI settings (board 9). Ensemble and the budget have no
 * platform default: off and no budget stand for it. */
export type Section = "scan" | "chat" | "ensemble" | "budget";

/** Does a section of the draft follow the platform default ("Same as platform default")?
 * `followed`: the reader it follows when it names none. */
export function sameAsDefault(draft: AiDraft, section: Section, followed: ScanReader): boolean {
  if (section === "ensemble") return !draft.ensemble;
  if (section === "budget") return draft.budget.trim() === "";
  return follows(aiBody(draft), followed)[section] === "all";
}

/** "Reset section": that section back to the platform default, the others as they are. */
export function resetSection(draft: AiDraft, section: Section): AiDraft {
  switch (section) {
    case "scan":
      return {
        ...draft,
        scan_models: [],
        scan_default: null,
        scan_thinking: null,
        scan_resolution: null,
        careful_thinking: null,
        careful_resolution: null,
        page_max_output_tokens: "",
        dynamic: null,
        scan_reader: null,
        mistral_model: null,
        ulc_ceiling: "",
      };
    case "chat":
      return { ...draft, chat_models: [], chat_default: null, chat_levels: [], chat_default_level: null };
    case "ensemble":
      return { ...draft, ensemble: false };
    case "budget":
      return { ...draft, budget: "" };
  }
}

/** Follow the platform defaults in both areas: no own models, levels or switches, and no
 * Ensemble (it is never a default). The budget and the daily page limit (#478) stay. */
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

// The most output tokens a page (#463, Tushar, 31 Oct 2026): what the server accepts.
export const MIN_PAGE_TOKENS = 1024;
export const MAX_PAGE_TOKENS = 65536;
export const TOKENS_HINT =
  "The most the AI may write and think for one page. Too low, and long pages get cut off and read again in two halves, which costs more.";
export { TOKENS_RANGE }; // the same words as the server's refusal (logic.ts)

/** Is the output limit shown? Mistral hides it with the Gemini fields; ultra low cost sets it. */
export const limitShown = (reader: ScanReader) => reader === "openrouter";

/** The most output tokens a page as a number, null to follow, or undefined when it is not one. */
export function parseTokens(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d{1,6}$/.test(trimmed)) return undefined;
  const value = Number(trimmed);
  return value >= MIN_PAGE_TOKENS && value <= MAX_PAGE_TOKENS ? value : undefined;
}

// The daily page limit (#478, Tushar, 3 Nov 2026): the pages AI may read in one India day.
export const MAX_PAGES_A_DAY = 1_000_000;
export const DAILY_PAGES_HINT =
  "The most pages AI may read in one day (India time). Past it, new files wait and are read when the limit resets, or as soon as you raise it. A file already being read finishes.";
export { DAILY_PAGES_RANGE };

/** The daily page limit as a number, null to follow (no limit on the platform), or undefined when it is not one. */
export function parseDailyPages(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d{1,7}$/.test(trimmed)) return undefined;
  const value = Number(trimmed);
  return value >= 1 && value <= MAX_PAGES_A_DAY ? value : undefined;
}

/** The ceiling in paise (#488), null to follow, or undefined when it is not ₹0.20 to ₹0.55. */
export function parseCeiling(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d{0,2}(\.\d{1,2})?$/.test(trimmed)) return undefined;
  const value = Math.round(Number(trimmed) * 100);
  return value >= MIN_CEILING_PAISE && value <= MAX_CEILING_PAISE ? value : undefined;
}

/** "₹0.40", from paise. */
export const ceilingText = (inPaise: number) => paise.format(inPaise / 100);

type CeilingEnv = Pick<AiEnv, "ulc_ceiling_paise" | "switch_ceiling_paise">;

/** The server's ceiling (#564 [V], review of #564): OCR_PAGE_CEILING_PAISE where the .env switch
 * supplies the reader (none saved), else ₹0.40 for an Ultra low cost saved with no ceiling. */
const serverCeiling = (saved: ScanReader | null, env: CeilingEnv): number =>
  (saved === null ? env.switch_ceiling_paise : null) ?? env.ulc_ceiling_paise;

/** The ceiling a scan really has: the draft's own, else the one it follows, else the server's. */
export const ceilingOf = (draft: Pick<AiDraft, "ulc_ceiling" | "scan_reader">, defaults: AiValues, env: CeilingEnv, platform: boolean): number =>
  parseCeiling(draft.ulc_ceiling) ??
  (platform ? null : defaults.ulc_ceiling_paise) ??
  serverCeiling(draft.scan_reader ?? (platform ? null : defaults.scan_reader), env);

/** "Pages read today: 1,240 of 2,000 ($0.42)." for the operator, the India day's counter, against
 * the limit in effect when there is one (#478 review, 3 Nov 2026), and that reading is paused once
 * it is reached (QA C4). */
export const pagesTodayText = (today: { pages: number; cost_usd: number | null }, limit: number | null = null) =>
  `Pages read today: ${formatNumber(today.pages)}${limit === null ? "" : ` of ${formatNumber(limit)}`}${
    today.cost_usd ? ` (${usd(today.cost_usd)})` : ""
  }.${limit !== null && today.pages >= limit ? " Reading is paused until the limit resets, or until you raise it." : ""}`;

/** Where a problem is shown: next to its field. "form" is the form's own line. */
export type AiField =
  | "scan_reader"
  | "ulc_ceiling_paise"
  | "mistral_model"
  | "scan_models"
  | "scan_default"
  | "page_max_output_tokens"
  | "daily_page_limit"
  | "chat_models"
  | "chat_default"
  | "chat_levels"
  | "budget"
  | "form";
export type AiErrors = Partial<Record<AiField, string>>;

/** The form's own checks, before a code is asked for or anything is sent. `reader`: the one
 * that really reads (readerOf); the output limit is checked only while it is shown, with Gemini
 * (the #488 review: a hidden one never stops a save). */
export function aiErrors(draft: AiDraft, platform: boolean, reader: ScanReader): AiErrors {
  const errors: AiErrors = {};
  if (draft.scan_models.length > 0 && (draft.scan_default === null || !draft.scan_models.includes(draft.scan_default)))
    errors.scan_default = "Choose the default scanning model from the ticked models.";
  if (draft.chat_models.length > 0 && (draft.chat_default === null || !draft.chat_models.includes(draft.chat_default)))
    errors.chat_default = "Choose the default answering model from the ticked models.";
  if (limitShown(reader) && parseTokens(draft.page_max_output_tokens) === undefined) errors.page_max_output_tokens = TOKENS_RANGE;
  if (parseDailyPages(draft.daily_page_limit) === undefined) errors.daily_page_limit = DAILY_PAGES_RANGE;
  if (parseCeiling(draft.ulc_ceiling) === undefined) errors.ulc_ceiling_paise = CEILING_RANGE;
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

/** The form's problems as said: one whose field isn't on the page (hidden while another reader
 * reads, or a model no longer listed) is said on the form's own line too, so Save never stops
 * without a word. `onPage`: is that field's control in the form now. */
export function shownErrors(errors: AiErrors, onPage: (field: AiField) => boolean): AiErrors {
  const hidden = (Object.keys(errors) as AiField[]).find((field) => field !== "form" && !onPage(field));
  return hidden && !errors.form ? { ...errors, form: errors[hidden] } : errors;
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
    scan_resolution: draft.scan_resolution,
    careful_thinking: draft.careful_thinking,
    careful_resolution: draft.careful_resolution,
    page_max_output_tokens: parseTokens(draft.page_max_output_tokens) ?? null,
    chat_levels: levels ? draft.chat_levels : null,
    chat_default_level: levels ? draft.chat_default_level : null,
    dynamic: draft.dynamic,
    scan_reader: draft.scan_reader,
    mistral_model: draft.mistral_model,
    daily_page_limit: parseDailyPages(draft.daily_page_limit) ?? null,
    ulc_ceiling_paise: parseCeiling(draft.ulc_ceiling) ?? null,
  };
}

/** The models a list offers: every enabled one that can do that work, and any already in it. */
export function offered(catalogue: OperatorModel[], area: Area, saved: string[]): OperatorModel[] {
  const can = area === "scan" ? canScan : canAnswer;
  return catalogue.filter((model) => (model.enabled_at !== null && can(model)) || saved.includes(model.id));
}

/** Does an organisation's own value follow the platform defaults? Per area: scanning is
 * its models, scan level, picture detail, pages read again and Dynamic; answering its
 * models and levels. `followed`: the reader it follows when it names none. */
export function follows(own: AiValues, followed: ScanReader): { scan: "all" | "some" | "none"; chat: "all" | "some" | "none" } {
  const part = (inherited: boolean[]) =>
    inherited.every(Boolean) ? "all" : inherited.some(Boolean) ? "some" : "none";
  return {
    scan: part([
      own.scan_models.length === 0,
      own.scan_thinking === null,
      own.scan_resolution === null,
      own.careful_thinking === null,
      own.careful_resolution === null,
      own.page_max_output_tokens === null,
      own.dynamic === null,
      own.scan_reader === null,
      // #473: the Mistral model counts unless the organisation reads with another reader itself;
      // #488 review: the ceiling only while ultra low cost really reads, its own or followed.
      ...(own.scan_reader === "openrouter" || own.scan_reader === "ultra_low_cost" ? [] : [own.mistral_model === null]),
      ...((own.scan_reader ?? followed) === "ultra_low_cost" ? [own.ulc_ceiling_paise === null] : []),
    ]),
    chat: part([own.chat_models.length === 0, own.chat_levels === null]),
  };
}

/** "Scanning: platform defaults · Answering: own settings" (C12). */
export function statusText(own: AiValues, followed: ScanReader): string {
  const words = { all: "platform defaults", some: "partly own settings", none: "own settings" };
  const area = follows(own, followed);
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
  field:
    | "scan_models"
    | "chat_models"
    | ScanSetting
    | ReaderSetting
    | "page_max_output_tokens"
    | "daily_page_limit"
    | "ulc_ceiling_paise"
    | "chat_levels"
    | "dynamic",
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
    case "scan_resolution":
    case "careful_thinking":
    case "careful_resolution":
      return settingWords(field, defaults) ?? `the server's level (${env[field]})`;
    case "scan_reader":
      return settingWords(field, defaults) ?? READER_WORDS[env.scan_reader];
    case "mistral_model":
      return settingWords(field, defaults, env.mistral_models) ?? mistralName(env.mistral_model, env.mistral_models);
    case "page_max_output_tokens":
      return formatNumber(defaults.page_max_output_tokens ?? env.page_max_output_tokens);
    case "daily_page_limit":
      return defaults.daily_page_limit === null ? "no limit" : `${formatNumber(defaults.daily_page_limit)} pages`;
    case "ulc_ceiling_paise":
      return ceilingText(defaults.ulc_ceiling_paise ?? serverCeiling(defaults.scan_reader, env));
    case "chat_levels":
      return levelText(defaults);
    case "dynamic":
      return defaults.dynamic ? "On" : "Off";
  }
}

/** The scan settings (#463) that each follow the platform, then the server, on their own. */
export type ScanSetting = "scan_thinking" | "scan_resolution" | "careful_thinking" | "careful_resolution";

/** Who reads the pages and which Mistral model (#473), each following on its own too. */
export type ReaderSetting = "scan_reader" | "mistral_model";

/** A scan setting's value in words ("Minimal", "High", "Mistral OCR"), or null when it follows. */
export function settingWords(
  field: ScanSetting | ReaderSetting,
  values: Pick<AiValues, ScanSetting | ReaderSetting>,
  models: MistralModel[] = [], // the server's Mistral models, for their names
): string | null {
  if (field === "scan_reader") return values.scan_reader === null ? null : READER_SHORT[values.scan_reader];
  if (field === "mistral_model") return values.mistral_model === null ? null : mistralName(values.mistral_model, models);
  if (field === "scan_resolution" || field === "careful_resolution") {
    const value = values[field];
    return value === null ? null : RESOLUTION_WORDS[value];
  }
  const level = values[field];
  return level === null ? null : LEVEL_WORDS[level];
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
  now(v: AiValues, catalogue: OperatorModel[], models: MistralModel[]): string;
}[] = [
  {
    area: "scan_models",
    label: "scanning models",
    changed: scanModelsDiffer,
    now: (v, catalogue) => (v.scan_models.length > 0 ? namesOf(v.scan_models, catalogue, v.scan_default) : "platform defaults"),
  },
  ...(
    [
      ["scan_reader", "who reads the pages"],
      ["mistral_model", "Mistral model"],
      ["scan_thinking", "scanning level"],
      ["scan_resolution", "picture detail"],
      ["careful_thinking", "thinking for pages read again"],
      ["careful_resolution", "picture detail for pages read again"],
    ] as const
  ).map(([area, label]) => ({
    area,
    label,
    changed: (a: AiValues, b: AiValues) => a[area] !== b[area],
    now: (v: AiValues, _catalogue: OperatorModel[], models: MistralModel[]) => settingWords(area, v, models) ?? "platform default",
  })),
  {
    area: "ulc_ceiling_paise", // #488
    label: CEILING_LABEL.toLowerCase(),
    changed: (a, b) => a.ulc_ceiling_paise !== b.ulc_ceiling_paise,
    now: (v) => (v.ulc_ceiling_paise === null ? "platform default" : ceilingText(v.ulc_ceiling_paise)),
  },
  {
    area: "page_max_output_tokens",
    label: "most output tokens a page",
    changed: (a, b) => a.page_max_output_tokens !== b.page_max_output_tokens,
    now: (v) => (v.page_max_output_tokens === null ? "platform default" : formatNumber(v.page_max_output_tokens)),
  },
  {
    area: "daily_page_limit",
    label: "daily page limit",
    changed: (a, b) => a.daily_page_limit !== b.daily_page_limit,
    now: (v) => (v.daily_page_limit === null ? "platform default" : `${formatNumber(v.daily_page_limit)} pages`),
  },
  {
    area: "dynamic",
    label: COST_SAVING,
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

/** The save bar's list (board 9): what saving the draft changes, in words; empty: nothing.
 * With the organisation's reply, the Mistral model is left out unless Mistral reads after the save,
 * and the ceiling unless ultra low cost does (#488). */
export function changesText(
  saved: AiDraft,
  draft: AiDraft,
  catalogue: OperatorModel[],
  reply?: Pick<OrgAiReply, "defaults" | "env">,
): string[] {
  const [before, after] = [aiBody(saved), aiBody(draft)];
  const reader = reply === undefined ? null : readerOf(draft, reply.defaults, reply.env, false);
  const leftOut = (area: string) =>
    reader !== null &&
    ((area === "mistral_model" && reader !== "mistral") || (area === "ulc_ceiling_paise" && reader !== "ultra_low_cost"));
  const changes = DEFAULT_AREAS.filter((each) => each.changed(before, after) && !leftOut(each.area)).map(
    (each) => `${capital(each.label)}: ${each.now(after, catalogue, reply?.env.mistral_models ?? [])}`,
  );
  if (saved.ensemble !== draft.ensemble) changes.push(`Ensemble: ${draft.ensemble ? "on" : "off"}`);
  const [was, now] = [budgetWords(saved.budget), budgetWords(draft.budget)];
  if (was !== now) changes.push(`Budget: ${was} to ${now}`);
  return changes;
}

const same = (a: string[], b: string[]) => [...a].sort().join("\n") === [...b].sort().join("\n");

/** Do two sets of values scan with different models, or a different default? */
export function scanModelsDiffer(a: AiValues, b: AiValues): boolean {
  return !same(a.scan_models, b.scan_models) || a.scan_default !== b.scan_default;
}

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
      return "scan_models";
    case "chat_not_verified":
      return "chat_models";
    case "limit_out_of_range":
      return "page_max_output_tokens";
    case "pages_out_of_range": // #478
      return "daily_page_limit";
    case "mistral_not_set_up": // #473
    case "ulc_needs_gemini": // #488
      return "scan_reader";
    case "ceiling_out_of_range": // #488
      return "ulc_ceiling_paise";
    default: // unavailable, default_not_allowed, level_not_supported: either area
      return "form";
  }
}

// --- The approximate cost a page and the accuracy (#463, Tushar, 31 Oct 2026) -----------

export const COST_DELAY_MS = 400; // the draft rests this long before the cost is asked
export const COST_WORKING = "Working out the cost…";
export const COST_NOT_MEASURED = "Not measured yet at these settings. Scan a few pages with them to see the cost.";
export const COST_FAILED = "Couldn't work out the cost right now.";
export const APPLIES_NOTE = "Applies to new scans and pages read again from now on. Text already read stays as it is.";
export const CLEAN_HINT = "Read cleanly means the AI wasn't unsure about any word.";
export const CHECK_ON_ORG = "Check accuracy on an organisation's page.";
export const CHECK_LABEL = "Check accuracy against max settings";
// The pages a check may read (council, 31 Oct 2026): more give a surer answer.
export const CHECK_PAGE_CHOICES = [20, 50, 100] as const;
export type CheckPages = (typeof CHECK_PAGE_CHOICES)[number];
export const CHECK_PAGES_HINT = "More pages give a surer answer. 100 is best before a big decision.";
export const OTHER_SETTINGS = "Your last check was at different settings.";
export const CHECK_RUNNING = "A check is already running for this organisation.";
export const TOO_MANY_CHECKS = "Too many checks in the last hour. Try again later.";
export const NO_PAGES = "This organisation has no scanned pages to check yet.";
// The #463 review (council, 31 Oct 2026).
export const COST_SAVED_MODELS = "This cost is for the saved models. Save to see the new one.";
export const LIMIT_NOT_MEASURED = "The limit's effect on cost isn't measured yet.";
export const CHECK_DIDNT_FINISH = "The last check didn't finish. Check again.";
export const TOO_FEW_PAGES = "Too few pages for a sure answer.";
const SURE_PAGES = 10; // fewer compared pages than this give no sure answer

/** What the cost endpoint has said for one set of scan settings: null while asking. */
export type CostReply = ScanCost | "failed" | null;

/** The cost endpoint's question for these scan settings; null fields are what a save inherits. */
export const costAsk = (customerId: string | null, values: ScanAsk): ScanCostAsk => ({
  customer_id: customerId,
  ...scanSettings(values),
});

// The settings a cost or an accuracy check is asked for: the four scan settings, the most
// output tokens a page, Cost saving, who reads the pages with which Mistral model (#473), and the
// most a page may cost (#488 review; null: the organisation's own, else the platform's).
// The scan models are never asked: the cost is the saved models'.
const SCAN_ASK = [
  "scan_thinking",
  "scan_resolution",
  "careful_thinking",
  "careful_resolution",
  "page_max_output_tokens",
  "dynamic",
  "scan_reader",
  "mistral_model",
  "ulc_ceiling_paise",
] as const;
export type ScanAsk = Pick<AiValues, (typeof SCAN_ASK)[number]>;

/** Just those settings (an accuracy check's body, with its pages and code). */
export const scanSettings = (values: ScanAsk): ScanAsk => ({
  scan_thinking: values.scan_thinking,
  scan_resolution: values.scan_resolution,
  careful_thinking: values.careful_thinking,
  careful_resolution: values.careful_resolution,
  page_max_output_tokens: values.page_max_output_tokens,
  dynamic: values.dynamic,
  scan_reader: values.scan_reader,
  mistral_model: values.mistral_model,
  ulc_ceiling_paise: values.ulc_ceiling_paise,
});

/** Do two sets of scan settings ask the same cost? */
export const sameSettings = (a: ScanAsk, b: ScanAsk) => SCAN_ASK.every((key) => a[key] === b[key]);

/** Does the draft differ from what is saved only in its most output tokens a page? The cost
 * is measured per settings, not per limit, so that change is not priced. */
export const onlyLimitDiffers = (saved: ScanAsk, draft: ScanAsk) =>
  saved.page_max_output_tokens !== draft.page_max_output_tokens &&
  sameSettings(saved, { ...draft, page_max_output_tokens: saved.page_max_output_tokens });

/**
 * Ask the cost once the draft has rested COST_DELAY_MS, then show the reply, or "failed"
 * once (no retry: the next change asks again). It shows null ("Working out the cost…") at
 * once, so an older figure never stands for these settings. The returned cancel, called
 * when the draft changes again, also drops the reply of an ask already out, so the last
 * reply wins.
 */
export function askScanCost(
  ask: ScanCostAsk,
  fetch: (ask: ScanCostAsk) => Promise<ScanCost>,
  show: (reply: CostReply) => void,
): () => void {
  let current = true;
  show(null);
  const timer = setTimeout(() => {
    fetch(ask).then(
      (reply) => current && show(reply),
      () => current && show("failed"),
    );
  }, COST_DELAY_MS);
  return () => {
    current = false;
    clearTimeout(timer);
  };
}

const perPage = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 4, maximumFractionDigits: 4 });
const paise = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const dayMonth = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

/** "₹0.24": dollars in rupees at INR_PER_USD. */
export const rupeeText = (dollars: number) => paise.format(dollars * INR_PER_USD);
/** "94%", and "under 1%" for a share above none that rounds to none. */
const percent = (share: number) => (share > 0 && share < 0.005 ? "under 1%" : `${Math.round(share * 100)}%`);

/** The Cost per page panel's words: the figure (large), the dollars, then the lines. */
export interface CostWords {
  figure: string | null;
  dollars: string | null;
  lines: string[];
}

export const MISTRAL_FIXED = "Mistral's list price, the same for every page.";
export const MISTRAL_BY_PAGE = "Mistral charges by the page, not by tokens.";

/** The cost a page: Mistral's fixed price (#473), what ultra low cost measured (#488), else the measured one. */
const pagePrice = (reply: ScanCost) =>
  reply.fixed_price ? reply.fixed_usd_per_page : reply.ulc ? reply.ulc.usd_per_page : reply.usd_per_page;

export function costWords(reply: CostReply): CostWords {
  if (reply === null) return { figure: null, dollars: null, lines: [COST_WORKING] };
  if (reply === "failed") return { figure: null, dollars: null, lines: [COST_FAILED] };
  const { first_read: first, re_read: again, days } = reply;
  const total = pagePrice(reply);
  const measured = (usd: number) =>
    `First read: ${perPage.format(usd)} a page, measured on ${countText(first.pages, "page", "pages")} in the last ${days} days.`;
  // Mistral (#473): its fixed price, with what was measured when there is some.
  if (reply.fixed_price && total !== null) {
    const lines = first.usd_per_page === null ? [MISTRAL_FIXED] : [MISTRAL_FIXED, measured(first.usd_per_page)];
    return { figure: `About ${rupeeText(total)} a page`, dollars: `${perPage.format(total)} a page`, lines };
  }
  if (total === null || first.usd_per_page === null) return { figure: null, dollars: null, lines: [COST_NOT_MEASURED] };
  const lines = [measured(first.usd_per_page)];
  const leftOut = "so the cost above leaves them out.";
  if (again === null) lines.push("Cost saving is off, so pages are read again only when someone asks.");
  else if (again.share === null)
    lines.push(
      again.usd_per_page === null
        ? `Read again: not measured yet at these settings, ${leftOut}`
        : `Read again: ${perPage.format(again.usd_per_page)} each, but how many pages need it is not known yet, ${leftOut}`,
    );
  else
    lines.push(
      again.usd_per_page === null
        ? `Read again: ${percent(again.share)} of pages, not measured yet at these settings, ${leftOut}`
        : `Read again: ${percent(again.share)} of pages, ${perPage.format(again.usd_per_page)} each.`,
    );
  return { figure: `About ${rupeeText(total)} a page`, dollars: `${perPage.format(total)} a page`, lines };
}

/** The panel's words while ultra low cost reads (#488): the ceiling large, then what it measured,
 * the dearest document, and the pages read locally to stay within the ceiling. */
export function ulcWords(reply: CostReply, ceilingPaise: number): CostWords {
  if (reply === null || reply === "failed") return costWords(reply);
  const figure = `At most ${ceilingText(ceilingPaise)} a page`;
  const seen = reply.ulc;
  if (!seen || seen.pages === 0 || seen.usd_per_page === null)
    return { figure, dollars: null, lines: [`Not measured yet: no page was read with ultra low cost in the last ${reply.days} days.`] };
  const lines = [
    `Measured: about ${rupeeText(seen.usd_per_page)} a page on ${countText(seen.pages, "page", "pages")} in the last ${reply.days} days.`,
  ];
  if (seen.top_usd_per_page !== null) lines.push(`Highest for one document: ${rupeeText(seen.top_usd_per_page)} a page.`);
  lines.push(`Read by Kaagaz's own reader to stay within the most a page may cost: ${percent(seen.limit_hits / seen.pages)} of pages.`);
  return { figure, dollars: null, lines };
}

/** The Tokens a page part of the panel: input, output with the 99th percentile, and the
 * pages that hit the limit; one line when nothing is measured. */
export function tokensWords(reply: ScanCost): string[] {
  if (reply.fixed_price) return [MISTRAL_BY_PAGE];
  const { input_per_page: input, output_per_page: output, output_p99: p99, limit_hits: hits, pages } = reply.tokens;
  if (pages === 0 || (input === null && output === null)) return ["Not measured yet at these settings."];
  const lines: string[] = [];
  if (input !== null) lines.push(`Input: about ${formatNumber(input)} a page (picture and instructions; lower it with Picture detail).`);
  if (output !== null)
    lines.push(`Output: about ${formatNumber(output)} a page${p99 === null ? "." : `; 99% of pages use under ${formatNumber(p99)}.`}`);
  lines.push(`Pages that hit the limit in the last ${reply.days} days: ${formatNumber(hits)} of ${formatNumber(pages)}.`);
  return lines;
}

export const TOKENS_LOW =
  "This is below what 1 in 100 pages needs. Those pages will be cut off and read again in two halves, which costs more and is slower.";

/** Is the draft's limit (its own, else the one the reply priced) below what 1 in 100 pages needs? */
export function tokensTooLow(reply: ScanCost, draftLimit: number | null): boolean {
  const p99 = reply.tokens.output_p99;
  return p99 !== null && (draftLimit ?? reply.tokens.limit) < p99;
}

export const TOKENS_UNDER_AVERAGE =
  "This is below what a page needs on average. Many pages will be cut off and read again in two halves, which costs more and is slower.";

/** While what 1 in 100 pages needs is not known: is the draft's limit (its own, else the
 * priced one) below what a page needs on average? */
export function tokensUnderAverage(reply: ScanCost, draftLimit: number | null): boolean {
  const { output_p99: p99, output_per_page: average, limit } = reply.tokens;
  return p99 === null && average !== null && (draftLimit ?? limit) < average;
}

/** Does the cost leave out the pages read again (Cost saving on, but their cost or share
 * not measured)? */
const reReadLeftOut = (reply: ScanCost) =>
  !reply.ulc &&
  reply.re_read !== null && (reply.re_read.usd_per_page === null || reply.re_read.share === null);

/** While the draft differs from what is saved and both are measured in full: "Now ₹0.31 →
 * ₹0.18 a page" and "42% cheaper", "35% dearer" or "about the same", in words (never colour
 * alone). None when either leaves the pages read again out: the two would not compare. */
export function costChange(saved: CostReply, draft: CostReply): { from: string; to: string; verdict: string } | null {
  if (saved === null || saved === "failed" || draft === null || draft === "failed") return null;
  if (reReadLeftOut(saved) || reReadLeftOut(draft)) return null;
  const [before, after] = [pagePrice(saved), pagePrice(draft)];
  if (before === null || after === null || before <= 0) return null;
  const change = Math.round((Math.abs(after - before) / before) * 100);
  const verdict = change === 0 ? "about the same" : `${change}% ${after < before ? "cheaper" : "dearer"}`;
  // #473: one side measured on Gemini, the other Mistral's list price; the verdict says so.
  const mixed = saved.fixed_price !== draft.fixed_price ? " (Gemini measured, Mistral list price)" : "";
  return { from: rupeeText(before), to: rupeeText(after), verdict: `${verdict}${mixed}` };
}

/** The free signal: "Pages read cleanly: 94% at these settings, 97% at max settings (last 30 days)". */
export function cleanText(reply: ScanCost): string {
  const { clean_share: share, pages, max_clean_share: max, max_pages: maxPages } = reply.quality;
  if (share === null || pages === 0) return "Not enough scans at these settings yet.";
  const atMax = max !== null && maxPages > 0 ? `, ${percent(max)} at max settings` : "";
  return `Pages read cleanly: ${percent(share)} at these settings${atMax} (last ${reply.days} days)`;
}

const isLevel = (level: string): level is ScanLevel => Object.hasOwn(LEVEL_WORDS, level);

/**
 * When the first read is not at the level asked, since the model doesn't take it: "Reads at
 * Low: this model doesn't take Minimal." Cost saving (re_read set) reads first at Low, or
 * Minimal when that is asked, so that is what it is held to. Null when it reads as asked, or
 * for a model with no levels (its one mode).
 */
export function levelNote(reply: ScanCost, asked: string): string | null {
  if (reply.fixed_price) return null; // Mistral has no thinking levels
  const read = reply.first_read.thinking;
  const expected = reply.re_read !== null && asked !== "minimal" ? "low" : asked;
  if (read === expected || !isLevel(read) || !isLevel(expected)) return null;
  return `Reads at ${LEVEL_WORDS[read]}: this model doesn't take ${LEVEL_WORDS[expected]}.`;
}

const checkedOn = (check: AccuracyCheck) => dayMonth.format(new Date(check.finished_at ?? check.started_at));

/** A finished check's result, numbers first, since a wrong amount matters more than a wrong
 * word (council, 31 Oct 2026): "Amounts, dates and numbers: 99% match max settings", then
 * "All words: 98%. Checked on 50 pages, 31 Oct."; `saved` is the Save confirmation's words.
 * Null when it has none (running, failed, or no words to compare). */
export function checkResult(check: AccuracyCheck): { figure: string; detail: string; saved: string } | null {
  if (check.status !== "done" || check.word_match === null) return null;
  const day = checkedOn(check);
  const notCompared = check.pages_failed > 0 ? ` ${countText(check.pages_failed, "page", "pages")} couldn't be compared.` : "";
  const tooFew = check.pages_checked < SURE_PAGES ? ` ${TOO_FEW_PAGES}` : "";
  const checked = `Checked on ${countText(check.pages_checked, "page", "pages")}, ${day}.${notCompared}${tooFew}`;
  const words = percent(check.word_match);
  if (check.numbers_match === null)
    return {
      figure: `All words: ${words} match max settings`,
      detail: `No numbers on the checked pages. ${checked}`,
      saved: `${words} of words match max settings (checked ${day})`,
    };
  const numbers = percent(check.numbers_match);
  return {
    figure: `Amounts, dates and numbers: ${numbers} match max settings`,
    detail: `All words: ${words}. ${checked}`,
    saved: `${numbers} of amounts, dates and numbers match max settings (checked ${day})`,
  };
}

/** The Accuracy part of an organisation's panel, after the free signal: a running check, a
 * last check that didn't finish, the result at these settings, or that the last check was
 * at other settings. */
export function accuracyWords(atThese: AccuracyCheck | null, latest: AccuracyCheck | null): { figure: string | null; line: string | null } {
  if (latest?.status === "running") return { figure: null, line: `Checking ${countText(latest.max_pages, "page", "pages")}…` };
  if (latest?.status === "failed") return { figure: null, line: CHECK_DIDNT_FINISH };
  const result = atThese ? checkResult(atThese) : null;
  if (result) return { figure: result.figure, line: result.detail };
  if (atThese) return { figure: null, line: "The last check at these settings did not finish. Check again." };
  return { figure: null, line: latest ? OTHER_SETTINGS : null };
}

/** The check's confirm: what it does and roughly what it costs for `pages`, from the last
 * check's measured cost a page (both reads); "a small cost" without one. */
export function checkEffect(orgName: string, latest: AccuracyCheck | null, pages: CheckPages, mistral = false): string {
  const perCheckedPage = latest && latest.cost_usd !== null && latest.pages_checked > 0 ? latest.cost_usd / latest.pages_checked : null;
  const cost = perCheckedPage === null ? "It has a small cost" : `It costs about ${rupeeText(pages * perCheckedPage)}`;
  // #473: max settings are Gemini's, even while Mistral reads.
  const gemini = mistral ? "To compare, this check also has Gemini read these pages at its highest settings. " : "";
  return (
    `Kaagaz reads ${pages} recent pages of ${orgName} twice, at these settings and at max, and compares the words. ` +
    `${gemini}${cost} and takes a few minutes. No text is shown or kept.`
  );
}

export const CHECK_MISTRAL_NOT_SET_UP =
  "Mistral isn't set up on this server, so this check can't read with Mistral. Choose Gemini as the reader to check now.";

/** A refused check in plain words: one running already, too many in the hour, no pages. A
 * refused code says what the AI settings save says. */
export function checkRefusal(caught: unknown): string {
  if (caught instanceof ApiError && caught.code === "mistral_not_set_up") return CHECK_MISTRAL_NOT_SET_UP; // #473
  if (caught instanceof ApiError && !Object.hasOwn(REFUSALS, caught.code)) {
    if (caught.status === 409) return CHECK_RUNNING;
    if (caught.status === 429) return TOO_MANY_CHECKS;
    if (caught.code === "no_pages") return NO_PAGES;
  }
  return operatorErrorMessage(caught);
}

/** Ask again while the check runs; stop once it has finished (or there is none). */
export const checkRunning = (check: AccuracyCheck | null) => check?.status === "running";

/** The Save confirmation with the cost a page now and, for an organisation, the accuracy:
 * "Saved …. Scans now cost about ₹0.18 a page, and 99% of amounts, dates and numbers match
 * max settings (checked 31 Oct)." Without a reply (it failed) the message alone. A cost
 * that leaves the pages read again out says so. */
export function withCost(message: string, reply: ScanCost | null, checks: boolean): string {
  if (reply === null) return message;
  const leftOut = reReadLeftOut(reply) ? " (pages read again not counted)" : "";
  const price = pagePrice(reply);
  const cost =
    price === null
      ? "The cost a page is not measured yet at these settings"
      : `Scans now cost about ${rupeeText(price)} a page${leftOut}`;
  if (!checks) return `${message} ${cost}.`;
  const result = reply.accuracy ? checkResult(reply.accuracy) : null;
  if (!result) return `${message} ${cost}. Accuracy against max settings isn't checked yet.`;
  return price === null ? `${message} ${cost}. ${result.saved}.` : `${message} ${cost}, and ${result.saved}.`;
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
