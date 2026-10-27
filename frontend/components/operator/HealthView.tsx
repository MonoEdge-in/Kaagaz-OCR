"use client";

// The platform's health, counts only: documents by status, failed ones by
// reason in plain words, how many are being read and how long the oldest has
// waited, for the whole platform and per organisation; and how many deleted
// organisations were removed for good and checked again a day later. PRD 5 U9:
// each worker's reader status (working, slow, down, key or model problem, or not
// reporting after 2 minutes) and the jobs waiting in each queue lane. #246 O1: the Release
// card, the commit the API runs; the deployer's last attempt and last good release are in
// files on the server that no container reads, so the card points to the deploy log.

import { useEffect, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { getHealth } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import type { PlatformHealth } from "@/lib/types";
import { LoadFailed } from "./LoadFailed";
import { countText, operatorErrorMessage, orgStatus } from "./logic";
import {
  commitWords,
  countsInWords,
  failureWords,
  queueWords,
  readerWords,
  statusWords,
  waitWords,
} from "./platform";
import admin from "@/components/admin/Admin.module.css";
import columns from "./Platform.module.css";
import styles from "./Operator.module.css";

const lastTime = (iso: string | null) => (iso ? `last ${formatDateTime(iso)}` : "none yet");

export function HealthView() {
  const [attempt, setAttempt] = useState(0);
  const [health, setHealth] = useState<PlatformHealth | null>(null);
  const [loadedAt, setLoadedAt] = useState(0); // Date.now() when it arrived: "Not reporting" is judged then
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    getHealth().then(
      (result) => {
        if (cancelled) return;
        setHealth(result);
        setLoadedAt(Date.now());
      },
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const failed = error?.attempt === attempt ? error.message : null;
  if (failed) return <LoadFailed what="the health" message={failed} onRetry={() => setAttempt((n) => n + 1)} />;
  if (!health) {
    return (
      <p className={styles.summary} role="status">
        Loading the health…
      </p>
    );
  }

  const { totals, purges } = health;
  const tiles = [
    { label: "Being read now", value: formatNumber(totals.in_progress) },
    { label: "Oldest waiting", value: waitWords(totals.oldest_in_progress_seconds) },
    ...(health.queues !== undefined ? queueWords(health.queues) : []),
    { label: `Deleted for good, ${lastTime(purges.last_purged_at)}`, value: formatNumber(purges.purged) },
    { label: `Checked again a day later, ${lastTime(purges.last_swept_at)}`, value: formatNumber(purges.swept) },
  ];
  return (
    <>
      <section className={admin.section} aria-labelledby="health-heading">
        <h2 id="health-heading" className={admin.sectionHeading}>
          Platform health
        </h2>
        <p className={styles.summary} role="status">
          Counts only, for {countText(health.organisations.length, "organisation", "organisations")}.
        </p>
        <dl className={admin.tiles}>
          {tiles.map((tile) => (
            <div key={tile.label} className={admin.tile}>
              <dt className={admin.tileLabel}>{tile.label}</dt>
              <dd className={`${admin.tileValue} tabular`}>{tile.value}</dd>
            </div>
          ))}
        </dl>
        <WordList heading="Documents by status" items={countsInWords(totals.documents_by_status, statusWords)} />
        <WordList heading="Failed, by reason" items={countsInWords(totals.failed_by_code, failureWords)} />
        {health.reader !== undefined && (
          <>
            <h3 className={admin.tilesHeading}>Reader, by worker</h3>
            {health.reader.length === 0 ? (
              <span className="muted">No worker has reported in the last 10 minutes.</span>
            ) : (
              <dl className={admin.tiles}>
                {health.reader.map((report) => (
                  <div key={report.worker} className={admin.tile}>
                    <dt className={admin.tileLabel}>{report.worker}</dt>
                    <dd className={admin.tileValue}>
                      {readerWords(report, loadedAt)}
                      {report.since && (
                        <span className={styles.statusNote}>since {formatDateTime(report.since)}</span>
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </>
        )}
      </section>
      {health.release !== undefined && (
        <section className={admin.section} aria-labelledby="health-release-heading">
          <h2 id="health-release-heading" className={admin.sectionHeading}>
            Release
          </h2>
          <dl className={admin.tiles}>
            <div className={admin.tile}>
              <dt className={admin.tileLabel}>Running commit</dt>
              <dd className={`${admin.tileValue} mono`} title={health.release.commit}>
                {commitWords(health.release.commit)}
              </dd>
            </div>
          </dl>
          <p className={styles.summary}>
            The last deploy and the last good release are in the server&apos;s deploy log
            (journalctl -u kaagaz-deploy).
          </p>
        </section>
      )}
      {health.organisations.length > 0 && (
        <section className={admin.section} aria-labelledby="health-orgs-heading">
          <h2 id="health-orgs-heading" className={admin.sectionHeading}>
            Per organisation
          </h2>
          <div className={`${table.reflowWrap} ${admin.tableScroll}`}>
            <table className={`table ${table.reflow}`} role="table">
              <caption className="visually-hidden">Health per organisation, counts only</caption>
              <thead role="rowgroup">
                <tr role="row">
                  <th scope="col" role="columnheader">Name</th>
                  <th scope="col" role="columnheader" className={columns.statusColumn}>Status</th>
                  <th scope="col" role="columnheader">Documents</th>
                  <th scope="col" role="columnheader">Failed, by reason</th>
                  <th scope="col" role="columnheader" className={columns.waitColumn}>Being read</th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {health.organisations.map((org) => {
                  const chip = orgStatus({ status: org.status, delete_after: null });
                  return (
                  <tr key={org.id} role="row">
                    <td role="cell" className={styles.orgName}>{org.name}</td>
                    <td role="cell" data-label="Status">
                      <span className="status-chip" data-tone={chip.tone}>
                        {chip.text}
                      </span>
                    </td>
                    <td role="cell" data-label="Documents">
                      <Words items={countsInWords(org.documents_by_status, statusWords)} none="None" />
                    </td>
                    <td role="cell" data-label="Failed, by reason">
                      <Words items={countsInWords(org.failed_by_code, failureWords)} none="None failed" />
                    </td>
                    <td role="cell" data-label="Being read">
                      {formatNumber(org.in_progress)}
                      {org.in_progress > 0 && (
                        <span className={styles.statusNote}>oldest {waitWords(org.oldest_in_progress_seconds)}</span>
                      )}
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

function Words({ items, none }: { items: string[]; none: string }) {
  if (items.length === 0) return <span className="muted">{none}</span>;
  return (
    <span className={styles.counts}>
      {items.map((item) => (
        <span key={item}>{item}</span>
      ))}
    </span>
  );
}

function WordList({ heading, items }: { heading: string; items: string[] }) {
  return (
    <>
      <h3 className={admin.tilesHeading}>{heading}</h3>
      <Words items={items} none="None" />
    </>
  );
}
