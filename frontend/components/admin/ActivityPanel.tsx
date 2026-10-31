"use client";

// The audit log, newest first, a page at a time: one table, reflowed into
// cards below 768 px. What each row was done on, and its details, are shown in
// words, never an id (UI quality council, 15 Oct 2026).
// File management A4 to A6 (Changed on purpose, PRD 1 screens): the columns When, Who,
// Action, Document and Folder, in the activity words (components/activity/words.ts); filter
// chips for person, folder (with its subfolders), file, action and date range, with Clear
// all; "Show more" by the reply's cursor; and Export to Excel, the filtered rows as a file.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import {
  NO_AUDIT_FILTERS,
  auditChipTexts,
  auditFilterCount,
  auditParams,
} from "@/components/activity/auditFilters";
import type { AuditFilters } from "@/components/activity/auditFilters";
import { ACTION_CHOICES, ACTIVITY_TEXT, activityTarget, activityWho, activityWords } from "@/components/activity/words";
import { buildTree, inTreeOrder, pathLabel } from "@/components/documents/folderTree";
import library from "@/components/documents/Library.module.css";
import table from "@/components/documents/Table.module.css";
import { WrapName } from "@/components/documents/WrapName";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { ApiError, auditExportUrl, listAudit, listDocuments, listFolders, listPeople } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import { formatDateTime } from "@/lib/format";
import type { ActivityEntry, DocumentSummary, Folder, Person } from "@/lib/types";
import { AUDIT_PAGE_SIZE, describeDetails } from "./logic";
import styles from "./Admin.module.css";

const SKELETON_ROWS = [0, 1, 2, 3, 4];

type Panel = "person" | "folder" | "file" | "action" | "date";

interface Loaded {
  key: string; // the filters it belongs to
  items: ActivityEntry[];
  next: string | null;
  total: number | null; // the older reply's total, paged by offset
}

export function ActivityPanel() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [filters, setFilters] = useState<AuditFilters>(NO_AUDIT_FILTERS);
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState<Loaded | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [people, setPeople] = useState<Person[] | null>(null);
  const [folders, setFolders] = useState<Folder[] | null>(null);
  const params = useMemo(() => auditParams(filters), [filters]);
  const key = `${JSON.stringify(params)}:${attempt}`;

  useEffect(() => {
    let cancelled = false;
    listPeople().then(
      (reply) => !cancelled && setPeople(reply.items),
      () => !cancelled && setPeople([]),
    );
    listFolders().then(
      (reply) => !cancelled && setFolders(reply.items),
      () => !cancelled && setFolders([]),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    listAudit({ ...params, limit: AUDIT_PAGE_SIZE }).then(
      (result) => {
        if (!cancelled) {
          setPage({ key, items: result.items, next: result.next_cursor ?? null, total: result.total ?? null });
          setError(null);
        }
      },
      (caught: unknown) => {
        if (!cancelled) setError({ key, message: errorText(caught) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [params, key]);

  const shown = page && page.key === key ? page : null;
  const failed = error !== null && error.key === key ? error.message : null;
  // The cursor when the reply gives one; else, on the older reply, the next offset.
  const hasMore = shown !== null && (shown.next !== null || (shown.total !== null && shown.items.length < shown.total));

  function more() {
    if (!shown) return;
    setBusy(true);
    const after = shown.next !== null ? { cursor: shown.next } : { offset: shown.items.length };
    listAudit({ ...params, ...after, limit: AUDIT_PAGE_SIZE }).then(
      (result) => {
        setPage({ ...shown, items: [...shown.items, ...result.items], next: result.next_cursor ?? null });
        setBusy(false);
      },
      (caught: unknown) => {
        setError({ key, message: errorText(caught) });
        setBusy(false);
      },
    );
  }

  function change(next: AuditFilters) {
    setFilters(next);
    headingRef.current?.focus();
  }

  return (
    <section className={styles.section} aria-labelledby="activity-heading">
      <div className={styles.activityHead}>
        <h2 id="activity-heading" ref={headingRef} tabIndex={-1} className={styles.sectionHeading}>
          {ACTIVITY_TEXT.heading}
        </h2>
        <a className="btn" href={auditExportUrl(params)} download>
          <Icon name="download" />
          {ACTIVITY_TEXT.export}
        </a>
      </div>
      <ActivityFilters filters={filters} onChange={change} people={people} folders={folders} />
      {failed && (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>Could not load the activity.</p>
            <p>{failed}</p>
          </div>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {!shown && !failed && (
        <>
          <p className="visually-hidden" role="status">
            {ACTIVITY_TEXT.loading}
          </p>
          <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
            {SKELETON_ROWS.map((row) => (
              <span key={row} className={`skeleton ${styles.skeletonRow}`} />
            ))}
          </div>
        </>
      )}
      {shown && shown.items.length === 0 && <p className={styles.emptyLine}>No activity yet.</p>}
      {shown && shown.items.length > 0 && <ActivityTable items={shown.items} />}
      {shown && shown.items.length > 0 && (
        <nav className={styles.pager} aria-label="Activity pages">
          <span className="muted tabular" aria-live="polite">
            {shown.total !== null ? `1–${shown.items.length} of ${shown.total}` : `${shown.items.length} shown`}
          </span>
          {hasMore && (
            <button type="button" className="btn" disabled={busy} onClick={more}>
              {ACTIVITY_TEXT.more}
              <Icon name="chevron-right" />
            </button>
          )}
        </nav>
      )}
    </section>
  );
}

/** The rows as a table (cards below 768 px). Exported for the render test. */
export function ActivityTable({ items }: { items: readonly ActivityEntry[] }) {
  const { columns } = ACTIVITY_TEXT;
  return (
    <div className={`${table.reflowWrap} ${styles.tableScroll}`}>
      <table className={`table ${table.reflow}`} role="table">
        <caption className="visually-hidden">Activity, newest first</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader">
              {columns.when}
            </th>
            <th scope="col" role="columnheader">
              {columns.who}
            </th>
            <th scope="col" role="columnheader">
              {columns.action}
            </th>
            <th scope="col" role="columnheader">
              {columns.document}
            </th>
            <th scope="col" role="columnheader">
              {columns.folder}
            </th>
            <th scope="col" role="columnheader">
              {columns.details}
            </th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {items.map((entry) => (
            <tr key={entry.id} role="row">
              <td role="cell" data-label={columns.when} className="tabular">
                <time dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time>
              </td>
              <td role="cell" data-label={columns.who}>
                {activityWho(entry)}
              </td>
              <td role="cell" data-label={columns.action} className={styles.strong}>
                <WrapName name={activityWords(entry)} />
              </td>
              <td role="cell" data-label={columns.document} className={styles.wrap}>
                {activityTarget(entry)}
              </td>
              <td role="cell" data-label={columns.folder} className={styles.wrap}>
                {entry.folder?.path ?? "—"}
              </td>
              <td role="cell" data-label={columns.details} className={`${styles.wrap} ${styles.details}`}>
                {describeDetails(entry.details) || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The chips: Person, Folder, File, Action, Date; one panel open at a time; Clear all (N). */
export function ActivityFilters({
  filters,
  onChange,
  people,
  folders,
}: {
  filters: AuditFilters;
  onChange(next: AuditFilters): void;
  people: readonly Person[] | null;
  folders: readonly Folder[] | null;
}) {
  const [open, setOpen] = useState<Panel | null>(null);
  const id = useId();
  const panelId = `${id}-panel`;
  const rowRef = useRef<HTMLDivElement>(null);
  const tree = useMemo(() => buildTree(folders ?? []), [folders]);
  const names = {
    person: filters.actorId === null ? null : (people?.find((p) => p.id === filters.actorId)?.name ?? "…"),
    folder: filters.folderId === null ? null : pathLabel(tree, filters.folderId) || "…",
  };
  const words = auditChipTexts(filters, names);
  const count = auditFilterCount(filters);

  const chip = (panel: Panel, text: string, active: boolean, remove: Partial<AuditFilters>) => (
    <span className={library.chipGroup} data-active={active ? "true" : undefined}>
      <button
        type="button"
        className={`${library.chip} ${library.chipOpens}`}
        data-active={active ? "true" : undefined}
        aria-expanded={open === panel}
        aria-controls={open === panel ? panelId : undefined}
        onClick={() => setOpen(open === panel ? null : panel)}
      >
        {text}
      </button>
      {active && (
        <IconButton
          icon="close"
          label={`Remove filter: ${text}`}
          className={library.chipRemove}
          onClick={() => {
            if (open === panel) setOpen(null);
            onChange({ ...filters, ...remove });
          }}
        />
      )}
    </span>
  );

  function closeOnEscape(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape" || open === null) return;
    rowRef.current?.querySelector<HTMLElement>('[aria-expanded="true"]')?.focus();
    setOpen(null);
  }

  function toggleAction(action: string) {
    const actions = filters.actions.includes(action)
      ? filters.actions.filter((chosen) => chosen !== action)
      : [...filters.actions, action];
    onChange({ ...filters, actions });
  }

  return (
    <div className={library.filters} onKeyDown={closeOnEscape}>
      <div ref={rowRef} className={library.filterRow} role="group" aria-label="Activity filters">
        {chip("person", words.person, filters.actorId !== null, { actorId: null })}
        {chip("folder", words.folder, filters.folderId !== null, { folderId: null })}
        {chip("file", words.file, filters.document !== null, { document: null })}
        {chip("action", words.action, filters.actions.length > 0, { actions: [] })}
        {chip("date", words.date, filters.from !== null || filters.to !== null, { from: null, to: null })}
        {count > 0 && (
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => {
              setOpen(null);
              onChange(NO_AUDIT_FILTERS);
            }}
          >
            {ACTIVITY_TEXT.clearAll} <span className="tabular">({count})</span>
          </button>
        )}
      </div>

      {open === "person" && (
        <div id={panelId} className={`motion-fade ${library.filterPanel}`}>
          <Choices
            legend={ACTIVITY_TEXT.person}
            name={`${id}-person`}
            any={ACTIVITY_TEXT.anyone}
            options={(people ?? []).map((person) => ({ id: person.id, label: person.name }))}
            value={filters.actorId}
            onPick={(actorId) => onChange({ ...filters, actorId })}
          />
        </div>
      )}
      {open === "folder" && (
        <div id={panelId} className={`motion-fade ${library.filterPanel}`}>
          <Choices
            legend={`${ACTIVITY_TEXT.folder} (with its subfolders)`}
            name={`${id}-folder`}
            any={ACTIVITY_TEXT.anyFolder}
            options={inTreeOrder(tree).map((node) => ({ id: node.folder.id, label: pathLabel(tree, node.folder.id) }))}
            value={filters.folderId}
            onPick={(folderId) => onChange({ ...filters, folderId })}
          />
        </div>
      )}
      {open === "file" && (
        <div id={panelId} className={`motion-fade ${library.filterPanel}`}>
          <FileFinder
            onPick={(document) => {
              setOpen(null);
              onChange({ ...filters, document });
            }}
          />
        </div>
      )}
      {open === "action" && (
        <div id={panelId} className={`motion-fade ${library.filterPanel}`}>
          <fieldset>
            <legend>{ACTIVITY_TEXT.action}</legend>
            <ul className={library.choices}>
              {ACTION_CHOICES.map((choice) => (
                <li key={choice.action}>
                  <label className={library.check}>
                    <input
                      type="checkbox"
                      checked={filters.actions.includes(choice.action)}
                      onChange={() => toggleAction(choice.action)}
                    />
                    {choice.label}
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        </div>
      )}
      {open === "date" && (
        <div id={panelId} className={`motion-fade ${library.filterPanel}`}>
          <fieldset>
            <legend>{ACTIVITY_TEXT.date}</legend>
            <div className={library.dates}>
              <div className="field">
                <label htmlFor={`${id}-from`}>From</label>
                <input
                  id={`${id}-from`}
                  className="input"
                  type="date"
                  value={filters.from ?? ""}
                  onChange={(event) => onChange({ ...filters, from: event.target.value || null })}
                />
              </div>
              <div className="field">
                <label htmlFor={`${id}-to`}>To (that day included)</label>
                <input
                  id={`${id}-to`}
                  className="input"
                  type="date"
                  value={filters.to ?? ""}
                  onChange={(event) => onChange({ ...filters, to: event.target.value || null })}
                />
              </div>
            </div>
          </fieldset>
        </div>
      )}
    </div>
  );
}

function Choices({
  legend,
  name,
  any,
  options,
  value,
  onPick,
}: {
  legend: string;
  name: string;
  any: string;
  options: { id: string; label: string }[];
  value: string | null;
  onPick(id: string | null): void;
}) {
  return (
    <fieldset>
      <legend>{legend}</legend>
      <ul className={library.choices}>
        {[{ id: null, label: any }, ...options].map((option) => (
          <li key={option.id ?? "any"}>
            <label className={library.check}>
              <input type="radio" name={name} checked={value === option.id} onChange={() => onPick(option.id)} />
              {option.label}
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

/** The File chip's panel: find a document by its name, then pick it. */
function FileFinder({ onPick }: { onPick(document: { id: string; name: string }): void }) {
  const id = useId();
  const [text, setText] = useState("");
  const [found, setFound] = useState<DocumentSummary[] | null>(null);
  const [findFailed, setFindFailed] = useState<string | null>(null); // #468: a failure is not "no file"
  function find() {
    const q = text.trim();
    if (q === "") return;
    setFindFailed(null);
    listDocuments({ q, limit: 8 }).then(
      (list) => setFound(list.items),
      (caught: unknown) => {
        setFound(null);
        setFindFailed(errorText(caught));
      },
    );
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    find();
  }
  return (
    <form onSubmit={submit}>
      <fieldset>
        <legend>{ACTIVITY_TEXT.file}</legend>
        <div className={library.dates}>
          <div className="field">
            <label htmlFor={id}>{ACTIVITY_TEXT.fileSearch}</label>
            <input
              id={id}
              className="input"
              type="search"
              value={text}
              autoComplete="off"
              onChange={(event) => setText(event.target.value)}
            />
          </div>
          <button type="submit" className={`btn ${library.applyButton}`}>
            Find
          </button>
        </div>
        {findFailed && (
          <div role="alert">
            <p className="error-text">
              Could not search the file names. {findFailed}
            </p>
            <button type="button" className="btn" onClick={find}>
              <Icon name="refresh" />
              Try again
            </button>
          </div>
        )}
        {found !== null && found.length === 0 && <p className="muted">{ACTIVITY_TEXT.noFiles}</p>}
        {found !== null && found.length > 0 && (
          <ul className={library.choices}>
            {found.map((document) => (
              <li key={document.id}>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => onPick({ id: document.id, name: document.display_name })}
                >
                  {document.display_name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </fieldset>
    </form>
  );
}

function errorText(caught: unknown): string {
  return caught instanceof ApiError ? errorWords(caught) : "Check your internet connection, then try again.";
}
