// The admin Activity tab's filters (File management A5, A6): person, folder (with its
// subfolders), file, action and date range, as chips and as the request. Pure functions,
// unit tested in node (tests/file-management.test.ts).

import type { AuditParams } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { ACTION_CHOICES, ACTIVITY_TEXT } from "./words";

export interface AuditFilters {
  actorId: string | null;
  folderId: string | null;
  document: { id: string; name: string } | null;
  actions: readonly string[];
  from: string | null; // YYYY-MM-DD, both days included
  to: string | null;
}

export const NO_AUDIT_FILTERS: AuditFilters = {
  actorId: null,
  folderId: null,
  document: null,
  actions: [],
  from: null,
  to: null,
};

/** The request for these filters (one page; the cursor is added by the caller). */
export function auditParams(filters: AuditFilters): AuditParams {
  return {
    user_id: filters.actorId ?? undefined,
    folder_id: filters.folderId ?? undefined,
    document_id: filters.document?.id ?? undefined,
    action: filters.actions.length > 0 ? filters.actions : undefined,
    from: filters.from ?? undefined,
    to: filters.to ?? undefined,
  };
}

/** How many chips are set: Clear all (N). */
export function auditFilterCount(filters: AuditFilters): number {
  return [
    filters.actorId !== null,
    filters.folderId !== null,
    filters.document !== null,
    filters.actions.length > 0,
    filters.from !== null || filters.to !== null,
  ].filter(Boolean).length;
}

const named = (name: string, value: string | null) => (value === null ? name : `${name}: ${value}`);

/** Each chip's words, saying what it is set to. */
export function auditChipTexts(
  filters: AuditFilters,
  names: { person: string | null; folder: string | null },
): { person: string; folder: string; file: string; action: string; date: string } {
  const actions = filters.actions.map(
    (action) => ACTION_CHOICES.find((choice) => choice.action === action)?.label ?? action,
  );
  const [first] = actions;
  const action =
    first === undefined ? null : actions.length === 1 ? first : `${first} and ${actions.length - 1} more`;
  const { from, to } = filters;
  const date =
    from !== null && to !== null
      ? `${formatDate(from)} to ${formatDate(to)}`
      : from !== null
        ? `From ${formatDate(from)}`
        : to !== null
          ? `Until ${formatDate(to)}`
          : null;
  return {
    person: named(ACTIVITY_TEXT.person, names.person),
    folder: named(ACTIVITY_TEXT.folder, names.folder),
    file: named(ACTIVITY_TEXT.file, filters.document?.name ?? null),
    action: named(ACTIVITY_TEXT.action, action),
    date: date ?? ACTIVITY_TEXT.date,
  };
}
