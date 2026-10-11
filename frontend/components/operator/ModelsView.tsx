"use client";

// Models, a tab of /operator (model choice, council 11 Oct 2026, MC1-MC3): the catalogue
// from OpenRouter's live list, refreshed daily, with each model's providers, live price
// per million tokens, whether it passes the catalogue's rules, whether verify-models
// passed for the reader's current prompt (and its measured cost a page), and whether it
// is enabled; every yes or no in words, never colour alone. Enable and Disable open
// ConfirmDialog with a fresh code (step-up), as the server asks.

import { useEffect, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { EmptyState } from "@/components/ui/EmptyState";
import { getOperatorModels } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { OperatorModel } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { modelConfirm, modelFocusId } from "./confirms";
import { LoadFailed } from "./LoadFailed";
import { afterDone, operatorErrorMessage } from "./logic";
import { modelAction, notEnabledReason, pageCostText, priceText, yesNo } from "./models";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

export function ModelsView() {
  const [attempt, setAttempt] = useState(0);
  const [models, setModels] = useState<OperatorModel[] | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [done, setDone] = useState("");

  useEffect(() => {
    let cancelled = false;
    getOperatorModels().then(
      (result) => !cancelled && setModels(result.items),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const failed = error?.attempt === attempt ? error.message : null;
  return (
    <section className={admin.section} aria-labelledby="models-heading">
      <h2 id="models-heading" className={admin.sectionHeading}>
        Models
      </h2>
      <p className={styles.summary}>
        From OpenRouter&apos;s live list, refreshed daily. A model can be enabled once it passes the rules and
        verify-models; then organisations can be given it.
      </p>
      <p className="visually-hidden" role="status">
        {done || (!models && !failed ? "Loading the models…" : "")}
      </p>
      {failed && !models && <LoadFailed what="the models" message={failed} onRetry={() => setAttempt((n) => n + 1)} />}
      {models && models.length === 0 && (
        <EmptyState icon="admin" title="No models yet">
          The worker reads OpenRouter&apos;s list once a day, or run refresh-models on the server.
        </EmptyState>
      )}
      {models && models.length > 0 && (
        <ModelsTable models={models} onChange={(model, enable) => setChanging(modelConfirm(model, enable))} />
      )}
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging((open) => afterDone(open, changing));
            setDone(message);
            setAttempt((n) => n + 1);
          }}
        />
      )}
    </section>
  );
}

export function ModelsTable({ models, onChange }: { models: OperatorModel[]; onChange(model: OperatorModel, enable: boolean): void }) {
  return (
    <div className={table.reflowWrap}>
      <table className={`table ${table.reflow}`} role="table">
        <caption className="visually-hidden">The model catalogue</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader">Model</th>
            <th scope="col" role="columnheader">Providers</th>
            <th scope="col" role="columnheader">Price per 1M tokens</th>
            <th scope="col" role="columnheader">Eligible</th>
            <th scope="col" role="columnheader">Verified</th>
            <th scope="col" role="columnheader">Enabled</th>
            <th scope="col" role="columnheader">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {models.map((model) => (
            <ModelRow key={model.id} model={model} onChange={(enable) => onChange(model, enable)} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ModelRow({ model, onChange }: { model: OperatorModel; onChange(enable: boolean): void }) {
  const action = modelAction(model);
  const reason = notEnabledReason(model);
  return (
    <tr role="row">
      <td role="cell" data-label="Model" className={styles.orgName}>
        <span id={modelFocusId(model.id)} tabIndex={-1}>
          {model.name}
        </span>
        <span className={styles.statusNote}>{model.id}</span>
      </td>
      <td role="cell" data-label="Providers">
        {model.providers.length > 0 ? model.providers.join(", ") : <span className="muted">None</span>}
      </td>
      <td role="cell" data-label="Price per 1M tokens" className="tabular">
        {priceText(model.price_in_usd_per_m)} in, {priceText(model.price_out_usd_per_m)} out
      </td>
      <td role="cell" data-label="Eligible">
        {yesNo(model.eligible)}
        {model.expires_on && <span className={styles.statusNote}>Ends {formatDate(model.expires_on)}</span>}
      </td>
      <td role="cell" data-label="Verified">
        {yesNo(model.verified)}
        <span className={styles.statusNote}>{pageCostText(model)}</span>
      </td>
      <td role="cell" data-label="Enabled">
        {model.enabled_at ? `Yes, since ${formatDate(model.enabled_at)}` : "No"}
        {reason && <span className={styles.statusNote}>{reason}</span>}
      </td>
      <td role="cell" data-label="Actions">
        {action && (
          <button
            type="button"
            className={action === "disable" ? "btn btn-danger" : "btn"}
            onClick={() => onChange(action === "enable")}
            aria-label={`${action === "enable" ? "Enable" : "Disable"} ${model.name}`}
          >
            {action === "enable" ? "Enable" : "Disable"}
          </button>
        )}
      </td>
    </tr>
  );
}
