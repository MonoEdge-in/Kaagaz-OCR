"use client";

// Activity across the platform, newest first: both audit logs (every
// organisation's and the operators' own), with the time, the organisation,
// the action in plain words, the target's TYPE and who acted as a kind only.
// The server never sends details, a target id, a person or a document, so
// none is shown. Filter by organisation and by action; Load more asks for the
// next page with the reply's cursor (keyset), so no row is repeated or skipped.

import { useEffect, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { listActivity, listOrgs } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import type { Organisation, PlatformEvent } from "@/lib/types";
import { LoadFailed } from "./LoadFailed";
import { operatorErrorMessage } from "./logic";
import { ACTION_FILTERS, actionWords, byWords, moreFor, targetWords } from "./platform";
import type { LoadMore } from "./platform";
import admin from "@/components/admin/Admin.module.css";
import columns from "./Platform.module.css";
import styles from "./Operator.module.css";

const COLUMNS: [string, string | undefined][] = [
  ["When", columns.whenColumn],
  ["Organisation", undefined],
  ["Action", undefined],
  ["Target", columns.targetColumn],
  ["By", columns.byColumn],
];

interface Page {
  key: string;
  items: PlatformEvent[];
  next: string | null;
}

export function ActivityView() {
  const [orgs, setOrgs] = useState<Organisation[]>([]);
  const [org, setOrg] = useState("");
  const [action, setAction] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [moreState, setMore] = useState<LoadMore>({ key: "", busy: false, failed: null });
  const key = `${org}|${action}|${attempt}`;

  useEffect(() => {
    listOrgs().then((result) => setOrgs(result.items), () => undefined); // the filter only: All still works
  }, []);

  useEffect(() => {
    let cancelled = false;
    listActivity({ org, action }).then(
      (result) => !cancelled && setPage({ key, items: result.items, next: result.next }),
      (caught: unknown) => !cancelled && setError({ key, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [org, action, key]);

  const current = page?.key === key ? page : null;
  const more = moreFor(moreState, key); // a Load more of another filter is forgotten
  const failed = !current && error?.key === key ? error.message : null;

  async function loadMore() {
    if (!current?.next || more.busy) return;
    setMore({ key, busy: true, failed: null });
    try {
      const result = await listActivity({ before: current.next, org, action });
      setPage((was) => (was?.key === key ? { key, items: [...was.items, ...result.items], next: result.next } : was));
      setMore({ key, busy: false, failed: null });
    } catch (caught) {
      setMore({ key, busy: false, failed: operatorErrorMessage(caught) });
    }
  }

  return (
    <section className={admin.section} aria-labelledby="activity-heading">
      <h2 id="activity-heading" className={admin.sectionHeading}>
        Activity
      </h2>
      <p className="muted">What happened, where and by whom (as a kind). Never a document, a person or details.</p>
      <div className={styles.toolbar}>
        <div className={`field ${styles.search}`}>
          <label htmlFor="activity-org">Organisation</label>
          <select id="activity-org" className="input" value={org} onChange={(event) => setOrg(event.target.value)}>
            <option value="">All organisations</option>
            {orgs.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </div>
        <div className={`field ${styles.search}`}>
          <label htmlFor="activity-action">Action</label>
          <select id="activity-action" className="input" value={action} onChange={(event) => setAction(event.target.value)}>
            {ACTION_FILTERS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className={styles.summary} role="status">
        {current
          ? current.items.length === 0
            ? "No activity matches."
            : `${formatNumber(current.items.length)} shown, newest first.`
          : failed
            ? ""
            : "Loading the activity…"}
      </p>
      {failed && <LoadFailed what="the activity" message={failed} onRetry={() => setAttempt((n) => n + 1)} />}
      {current && current.items.length > 0 && (
        <div className={table.reflowWrap}>
          <table className={`table ${table.reflow}`} role="table">
            <caption className="visually-hidden">Activity, newest first</caption>
            <thead role="rowgroup">
              <tr role="row">
                {COLUMNS.map(([label, className]) => (
                  <th key={label} scope="col" role="columnheader" className={className}>
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody role="rowgroup">
              {current.items.map((event, index) => (
                <tr key={`${event.source}-${event.at}-${index}`} role="row">
                  <td role="cell" data-label="When" className="tabular">
                    <time dateTime={event.at}>{formatDateTime(event.at)}</time>
                  </td>
                  <td role="cell" data-label="Organisation" className={styles.email}>
                    {event.organisation_name ?? (event.source === "platform" ? "Platform" : "Deleted organisation")}
                  </td>
                  <td role="cell" data-label="Action" className={styles.orgName}>
                    {actionWords(event.action)}
                  </td>
                  <td role="cell" data-label="Target">
                    {targetWords(event.target_type)}
                  </td>
                  <td role="cell" data-label="By">
                    {byWords(event.by)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {more.failed && (
        <p className="error-text" role="alert">
          {more.failed}
        </p>
      )}
      {current?.next && (
        <div>
          <button type="button" className="btn" onClick={loadMore} disabled={more.busy} aria-busy={more.busy || undefined}>
            {more.busy && <span className="spinner" aria-hidden="true" />}
            Load more
          </button>
        </div>
      )}
    </section>
  );
}
