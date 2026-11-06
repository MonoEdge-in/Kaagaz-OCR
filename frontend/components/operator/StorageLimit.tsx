"use client";

// Storage limit (#516): how much an organisation may store in all, in whole GB, 1 to 10,000;
// every file, every version and the Trash until it is emptied count. The platform's default sits
// on the Folder uploads tab (StorageLimitView); one organisation's own value opens from its Manage
// menu (OrgStorageLimitDialog), with what it has used. An empty field follows the platform (an
// organisation's) or the default, 50 GB (the platform's). The server refuses a new upload that
// would go past it; nothing already stored is touched. Every save asks first in ConfirmDialog with
// a fresh code (step-up). Super admin only. Built like FolderUploadLimits.tsx.

import { useEffect, useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Toast } from "@/components/ui/Toast";
import { getStorageLimit, setStorageLimit } from "@/lib/api";
import { formatNumber, storageUsedText } from "@/lib/format";
import type { Organisation, StorageLimit } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { LoadFailed } from "./LoadFailed";
import { afterDone, operatorErrorMessage } from "./logic";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

const EXPLAIN =
  "How much one organisation may store in all: every file, every version, and the Trash until it is emptied. Kaagaz refuses a new upload that would go past it.";
const SAVE_ID = "storage-limit-save";
const FIELD_ID = "storage-limit-gb";
const gbText = (gb: number) => `${formatNumber(gb)} GB`;
/** What an empty organisation field follows: the platform's own value, else the default. */
const platformGb = (value: StorageLimit) => value.platform_gb ?? value.default_gb;

/** The field's value: null for empty, a whole number from 1 to `most`, or undefined when it is neither. */
export function parseStorageGb(text: string, most: number): number | null | undefined {
  const value = text.trim();
  if (value === "") return null;
  if (!/^\d+$/.test(value)) return undefined;
  const number = Number(value);
  return number >= 1 && number <= most ? number : undefined;
}

/** Saving: asked first, with a fresh code. `value` is the loaded reply, for what empty means. */
export function storageConfirm(gb: number | null, value: StorageLimit, org?: Organisation): Confirm {
  const which = org ? `the storage limit for ${org.name}` : "the platform's storage limit";
  const to =
    gb !== null ? gbText(gb) : org ? `follow the platform (${gbText(platformGb(value))})` : `the default (${gbText(value.default_gb)})`;
  const limit = gbText(gb ?? (org ? platformGb(value) : value.default_gb));
  const whose = org ? org.name : "an organisation without its own limit";
  const done = gb === null && org ? `follows the platform (${limit})` : `is ${limit}`;
  return {
    title: `Set ${which} to ${to}?`,
    effect: `New uploads that would take ${whose} past ${limit} are refused. Nothing already stored is touched.`,
    action: "Save",
    done: `${which.charAt(0).toUpperCase()}${which.slice(1)} ${done}.`,
    danger: false,
    stepUp: true,
    focusId: SAVE_ID,
    run: (code) => setStorageLimit(org ? org.id : null, gb, code),
  };
}

/** The one field and Save, which opens the confirm; an organisation's also says what it has used. */
export function StorageLimitForm(props: { value: StorageLimit; org: boolean; onSave(gb: number | null): void }) {
  const { value, org, onSave } = props;
  const [draft, setDraft] = useState(value.storage_gb === null ? "" : String(value.storage_gb));
  const parsed = parseStorageGb(draft, value.most_gb);
  const bad = parsed === undefined;
  const hint = org
    ? `Empty: follows the platform default (${gbText(platformGb(value))})`
    : `Empty: uses the default (${gbText(value.default_gb)})`;
  return (
    <div className={styles.dialogForm}>
      {org && <p className={styles.summary}>{storageUsedText(value.used_bytes, value.effective_gb)}</p>}
      <div className="field">
        <label htmlFor={FIELD_ID}>Storage (GB)</label>
        <input
          id={FIELD_ID}
          className="input"
          inputMode="numeric"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-invalid={bad ? true : undefined}
          aria-describedby={`${FIELD_ID}-hint${bad ? ` ${FIELD_ID}-error` : ""}`}
        />
        <span id={`${FIELD_ID}-hint`} className="hint">
          {hint}
        </span>
        {bad && (
          <span id={`${FIELD_ID}-error`} className="error-text" role="alert">
            Enter a whole number from 1 to {formatNumber(value.most_gb)}, or leave it empty.
          </span>
        )}
      </div>
      <div>
        <button
          id={SAVE_ID}
          type="button"
          className="btn btn-primary"
          disabled={bad || parsed === value.storage_gb}
          onClick={() => onSave(parsed as number | null)}
        >
          Save
        </button>
      </div>
    </div>
  );
}

/** Loads the platform's value (no org) or one organisation's, again after each save. Exported for its tests. */
export function useStorageLimit(orgId?: string) {
  const [attempt, setAttempt] = useState(0);
  const [value, setValue] = useState<StorageLimit | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  useEffect(() => {
    let cancelled = false;
    getStorageLimit(orgId).then(
      (result) => !cancelled && setValue(result),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [orgId, attempt]);
  const failed = error?.attempt === attempt ? error.message : null;
  // A failed load (after a save, too) shows that it failed, never the value from before it.
  return { value: failed ? null : value, failed, reload: () => setAttempt((n) => n + 1) };
}

/** The platform's default, on the Folder uploads tab. */
export function StorageLimitView() {
  const { value, failed, reload } = useStorageLimit();
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  return (
    <section className={admin.section} aria-labelledby="storage-limit-heading">
      <h2 id="storage-limit-heading" className={admin.sectionHeading}>
        Storage
      </h2>
      <p className={styles.summary}>{EXPLAIN} An organisation&apos;s own limit, set from its Manage menu, comes first.</p>
      {failed && !value && <LoadFailed what="the storage limit" message={failed} onRetry={reload} />}
      {!value && !failed && (
        <p className="visually-hidden" role="status">
          Loading the storage limit…
        </p>
      )}
      {value && (
        <StorageLimitForm
          key={String(value.storage_gb)}
          value={value}
          org={false}
          onSave={(gb) => setChanging(storageConfirm(gb, value))}
        />
      )}
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging((open) => afterDone(open, changing));
            setToast(message);
            reload();
          }}
        />
      )}
      <Toast message={toast} onClose={() => setToast(null)} />
    </section>
  );
}

/** The Manage menu's "Storage limit" for one organisation: super admin only. `onDone`: each
 * change's words, for the page's Toast too, so they outlast the dialog (as OrgFolderUploadLimitsDialog). */
export function OrgStorageLimitDialog({ org, onClose, onDone }: { org: Organisation; onClose(): void; onDone(message: string): void }) {
  const { value, failed, reload } = useStorageLimit(org.id);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [done, setDone] = useState("");
  return (
    <>
      <ActionDialog title={`Storage limit for ${org.name}`} onClose={onClose}>
        <div className={styles.dialogForm}>
          <p className={styles.summary}>{EXPLAIN} This organisation only.</p>
          <p className="visually-hidden" role="status">
            {done || (!value && !failed ? "Loading the storage limit…" : "")}
          </p>
          {failed && !value && <LoadFailed what="the storage limit" message={failed} onRetry={reload} />}
          {value && (
            <StorageLimitForm
              key={`${value.storage_gb}:${value.used_bytes}`}
              value={value}
              org
              onSave={(gb) => setChanging(storageConfirm(gb, value, org))}
            />
          )}
        </div>
      </ActionDialog>
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging((open) => afterDone(open, changing));
            setDone(message);
            onDone(message);
            reload();
          }}
        />
      )}
    </>
  );
}
