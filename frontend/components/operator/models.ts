// The operator's Models and AI spend tabs and the "AI models" dialog (model choice,
// council 11 Oct 2026, MC2, MC3, MS1, MS2, SP3, SP4): the words for a model and for
// money, the dialog's checks, the spend periods and totals. Kept pure, so vitest can
// prove them.

import { formatUsd } from "@/lib/format";
import type { OperatorModel, OperatorOrgModels, OperatorUsageRow, UsageTotals } from "@/lib/types";

const TOKENS_PER_M = 1_000_000;
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

/**
 * MC3: what one page costs at the live price: from the pages scanned in the last 30
 * days (ledger_tokens_*); else from verify-models' small test page, a rough estimate;
 * null with neither.
 */
export function pageCost(model: OperatorModel): { usd: number; rough: boolean } | null {
  const rough = model.ledger_tokens_in === null || model.ledger_tokens_out === null;
  const tokensIn = rough ? model.tokens_per_page_in : model.ledger_tokens_in;
  const tokensOut = rough ? model.tokens_per_page_out : model.ledger_tokens_out;
  if (tokensIn === null || tokensOut === null) return null;
  return {
    usd: (tokensIn * model.price_in_usd_per_m + tokensOut * model.price_out_usd_per_m) / TOKENS_PER_M,
    rough,
  };
}

/** The cost a page in words, as the upload's "Read with" says it. */
export function pageCostText(model: OperatorModel): string {
  const cost = pageCost(model);
  if (cost === null) return "Cost not measured yet";
  const text = cost.usd < 0.01 ? "under $0.01 a page" : `about ${usd(cost.usd)} a page`;
  return cost.rough ? `${text} (rough estimate)` : text;
}

export const yesNo = (value: boolean) => (value ? "Yes" : "No");

/** Enable only an eligible model verified for the reader's current prompt, as the server. */
export const canEnable = (model: OperatorModel) => model.eligible && model.verified && model.enabled_at === null;

/** What the operator can do to a model now: enable it, disable it, or nothing. */
export function modelAction(model: OperatorModel): "enable" | "disable" | null {
  if (model.enabled_at !== null) return "disable";
  return canEnable(model) ? "enable" : null;
}

/** Why a model can't be enabled, in plain words; null when it can (or is). */
export function notEnabledReason(model: OperatorModel): string | null {
  if (model.enabled_at !== null || canEnable(model)) return null;
  if (!model.eligible) return "Does not pass the catalogue's rules now.";
  return "Run verify-models --record for it first.";
}

// --- The "AI models" dialog ----------------------------------------------------------

export interface OrgModelsDraft {
  models: string[];
  default_scan: string | null;
  budget: string; // as typed; empty: no budget
}

export const draftOf = (saved: OperatorOrgModels): OrgModelsDraft => ({
  models: [...saved.models],
  default_scan: saved.default_scan,
  budget: saved.ai_budget_usd === null ? "" : String(saved.ai_budget_usd),
});

/** Tick or untick a model. The first model ticked becomes the default; unticking the
 * default moves it to the first model left, or to none. */
export function toggled(draft: OrgModelsDraft, id: string, on: boolean): OrgModelsDraft {
  const models = on ? [...new Set([...draft.models, id])] : draft.models.filter((model) => model !== id);
  const keep = draft.default_scan !== null && models.includes(draft.default_scan);
  return { ...draft, models, default_scan: keep ? draft.default_scan : (models[0] ?? null) };
}

/** The budget as a number of dollars, null for none, or undefined when it is not one. */
export function parseBudget(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d{1,10}(\.\d{1,2})?$/.test(trimmed)) return undefined;
  const value = Number(trimmed);
  return value > 0 && value <= MAX_BUDGET ? value : undefined;
}

export interface DraftErrors {
  default_scan: string | null;
  budget: string | null;
}

/** The dialog's own checks, before anything is sent. */
export function draftErrors(draft: OrgModelsDraft): DraftErrors {
  const default_scan =
    draft.models.length > 0 && (draft.default_scan === null || !draft.models.includes(draft.default_scan))
      ? "Choose the default scan model from the ticked models."
      : null;
  const budget = parseBudget(draft.budget) === undefined ? "Enter dollars above 0, with at most 2 decimals, or leave it empty." : null;
  return { default_scan, budget };
}

/** What PUT /operator/orgs/{id}/models is sent; call it only when draftErrors finds none. */
export function bodyOf(draft: OrgModelsDraft): OperatorOrgModels {
  return {
    models: draft.models,
    default_scan: draft.models.length === 0 ? null : draft.default_scan,
    ai_budget_usd: parseBudget(draft.budget) ?? null,
  };
}

/** The models the dialog offers: every enabled one, and any the organisation still has. */
export const offered = (catalogue: OperatorModel[], saved: string[]) =>
  catalogue.filter((model) => model.enabled_at !== null || saved.includes(model.id));

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

/** Every row added up: the cost is not known when no row's is, and estimated when any row's is. */
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
  };
}
