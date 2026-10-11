"use client";

// The organisation's AI models, read-only (model choice MS1, MS2, brief §7):
// MonoEdge (the operator) picks them and the default, so nothing here changes
// them. Each model's price per million tokens and, once verify-models has
// measured it, about what a page costs; then this month's spend against the
// budget (SP4). The view is ModelsView, rendered in node by
// tests/admin-models.test.ts.

import { useEffect, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { Icon } from "@/components/ui/Icon";
import { ApiError, getAdminModels, getAdminUsage } from "@/lib/api";
import { formatUsd } from "@/lib/format";
import type { OrgModels, UsageBudget } from "@/lib/types";
import { budgetLine } from "./logic";
import styles from "./Admin.module.css";

const COLUMNS = ["Model", "Provider", "Input, per million tokens", "Output, per million tokens", "Cost"];

export function ModelsView({ models, budget }: { models: OrgModels; budget: UsageBudget }) {
  return (
    <>
      {models.models.length === 0 ? (
        <p className={styles.emptyLine}>
          MonoEdge has not chosen models for your organisation, so scans are read by the platform&apos;s
          model, {models.default_scan}. Ask MonoEdge to change this.
        </p>
      ) : (
        <div className={table.reflowWrap}>
          <table className={`table ${table.reflow}`} role="table">
            <caption className={styles.caption}>Set by MonoEdge. Ask MonoEdge to change these.</caption>
            <thead role="rowgroup">
              <tr role="row">
                {COLUMNS.map((name) => (
                  <th key={name} scope="col" role="columnheader">
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody role="rowgroup">
              {models.models.map((model) => (
                <tr key={model.id} role="row">
                  <td role="cell" data-label="Model" className={styles.wrap}>
                    <span className={styles.strong}>{model.name}</span>
                    {model.is_default_scan && (
                      <span className={styles.defaultMark}>
                        <Icon name="check-seal" size={16} />
                        Default
                      </span>
                    )}
                  </td>
                  <td role="cell" data-label="Provider" className={styles.wrap}>
                    {model.provider ?? "Not known"}
                  </td>
                  <td role="cell" data-label="Input, per million tokens" className="tabular">
                    {formatUsd(model.price_in_usd_per_m)}
                  </td>
                  <td role="cell" data-label="Output, per million tokens" className="tabular">
                    {formatUsd(model.price_out_usd_per_m)}
                  </td>
                  <td role="cell" data-label="Cost" className="tabular">
                    {typeof model.usd_per_page === "number"
                      ? `about ${formatUsd(model.usd_per_page)} a page`
                      : "Cost not measured yet"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="tabular">
        <span className={styles.strong}>Monthly AI budget: </span>
        {budgetLine({ ai_budget_usd: models.ai_budget_usd, spent_this_month_usd: budget.spent_this_month_usd })}
      </p>
    </>
  );
}

export function ModelsPanel() {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ models: OrgModels; budget: UsageBudget } | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getAdminModels(), getAdminUsage()]).then(
      ([models, usage]) => {
        if (!cancelled) setLoaded({ models, budget: usage.budget });
      },
      (caught: unknown) => {
        if (!cancelled) {
          setError({
            attempt,
            message:
              caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.",
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const failed = error !== null && error.attempt === attempt && !loaded ? error.message : null;

  return (
    <section className={styles.section} aria-labelledby="models-heading">
      <h2 id="models-heading" className={styles.sectionHeading}>
        AI models
      </h2>
      {failed && (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>Could not load the AI models.</p>
            <p>{failed}</p>
          </div>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {!loaded && !failed && (
        <p className="delayed muted" role="status">
          Loading the AI models…
        </p>
      )}
      {loaded && <ModelsView models={loaded.models} budget={loaded.budget} />}
    </section>
  );
}
