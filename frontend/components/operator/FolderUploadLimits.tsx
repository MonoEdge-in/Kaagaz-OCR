"use client";

// Folder uploads, a tab of /operator (#484): the most files, the most total size and the most
// folders one folder upload may have, for every organisation; one organisation's own values open
// from its Manage menu (OrgFolderUploadLimitsDialog). An empty field follows the platform (an
// organisation's) or the platform's default (the platform's); each field says which, and an
// organisation's own value is marked Own. The server checks every upload against them. Every
// save asks first in ConfirmDialog with a fresh code (step-up). Super admin only. Built like
// SpeedControls.tsx and SessionTimeout.tsx.

import { useEffect, useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Toast } from "@/components/ui/Toast";
import { getFolderUploadLimits, getOrgFolderUploadLimits, setFolderUploadLimits, setOrgFolderUploadLimits } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { FolderLimitName, FolderLimits, Organisation } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { LoadFailed } from "./LoadFailed";
import { afterDone, operatorErrorMessage } from "./logic";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

const NAMES: FolderLimitName[] = ["max_files", "max_gb", "max_folders"];
const LIMIT_LABELS: Record<FolderLimitName, string> = {
  max_files: "Most files in one folder upload",
  max_gb: "Most total size, in GB",
  max_folders: "Most folders one upload may make",
};
/** The highest value each field takes, as the server's checks. */
const LIMIT_CEILINGS: Record<FolderLimitName, number> = { max_files: 200_000, max_gb: 20, max_folders: 50_000 };
const EXPLAIN = "The most one folder upload may hold. Kaagaz checks each upload against these before anything is sent.";
const SAVE_ID = "folder-limits-save";
const fieldId = (name: FolderLimitName) => `folder-limit-${name}`;

/** "60,000 files", "5 GB", "10,000 folders". */
function limitText(name: FolderLimitName, value: number): string {
  if (name === "max_gb") return `${formatNumber(value)} GB`;
  return `${formatNumber(value)} ${name === "max_files" ? "files" : "folders"}`;
}

/** What an empty organisation field follows: the platform's value, else the platform's default. */
export function inheritedLabel(name: FolderLimitName, platform: FolderLimits, defaults: Record<FolderLimitName, number>): string {
  const value = platform[name];
  return value === null
    ? `From the platform's default: ${limitText(name, defaults[name])}`
    : `From the platform: ${limitText(name, value)}`;
}

/** A field's value: null for empty, a whole number from 1 to the ceiling, or undefined when it is neither. */
export function parseLimit(text: string, name: FolderLimitName): number | null | undefined {
  const value = text.trim();
  if (value === "") return null;
  if (!/^\d+$/.test(value)) return undefined;
  const number = Number(value);
  return number >= 1 && number <= LIMIT_CEILINGS[name] ? number : undefined;
}

/** Saving: asked first, with a fresh code, naming each field that changes. */
export function limitsConfirm(next: FolderLimits, saved: FolderLimits, org?: Organisation): Confirm {
  const empty = org ? "from the platform" : "from the platform's default";
  const changes = NAMES.filter((name) => next[name] !== saved[name]).map((name) => {
    const value = next[name];
    return `${LIMIT_LABELS[name]}: ${value === null ? empty : formatNumber(value)}.`;
  });
  const of = org ? ` for ${org.name}` : "";
  return {
    title: `Save the folder upload limits${of}?`,
    effect: `${changes.join(" ")} Folder uploads started from now on are checked against them.`,
    action: "Save",
    done: `The folder upload limits${of} are saved.`,
    danger: false,
    stepUp: true,
    focusId: SAVE_ID,
    run: (code) => (org ? setOrgFolderUploadLimits(org.id, next, code) : setFolderUploadLimits(next, code)),
  };
}

/** The three fields and Save, which opens the confirm. `inherited` is what an empty field follows. */
export function FolderLimitsForm(props: {
  saved: FolderLimits;
  inherited: FolderLimits;
  defaults: Record<FolderLimitName, number>;
  org: boolean;
  onSave(limits: FolderLimits): void;
}) {
  const { saved, inherited, defaults, org, onSave } = props;
  const [draft, setDraft] = useState<Record<FolderLimitName, string>>(() => ({
    max_files: saved.max_files === null ? "" : String(saved.max_files),
    max_gb: saved.max_gb === null ? "" : String(saved.max_gb),
    max_folders: saved.max_folders === null ? "" : String(saved.max_folders),
  }));
  const parsed = {
    max_files: parseLimit(draft.max_files, "max_files"),
    max_gb: parseLimit(draft.max_gb, "max_gb"),
    max_folders: parseLimit(draft.max_folders, "max_folders"),
  };
  const valid = NAMES.every((name) => parsed[name] !== undefined);
  const changed = NAMES.some((name) => parsed[name] !== saved[name]);
  return (
    <div className={styles.dialogForm}>
      {NAMES.map((name) => {
        const bad = parsed[name] === undefined;
        const follows = org ? inheritedLabel(name, inherited, defaults) : `Using the default: ${limitText(name, defaults[name])}.`;
        const hint = saved[name] === null ? follows : org ? `Own. Leave it empty to follow the platform.` : `Leave it empty to use the default: ${limitText(name, defaults[name])}.`;
        return (
          <div key={name} className="field">
            <label htmlFor={fieldId(name)}>{LIMIT_LABELS[name]}</label>
            <input
              id={fieldId(name)}
              className="input"
              inputMode="numeric"
              value={draft[name]}
              onChange={(event) => setDraft({ ...draft, [name]: event.target.value })}
              aria-invalid={bad ? true : undefined}
              aria-describedby={`${fieldId(name)}-hint${bad ? ` ${fieldId(name)}-error` : ""}`}
            />
            <span id={`${fieldId(name)}-hint`} className="hint">
              {hint}
            </span>
            {bad && (
              <span id={`${fieldId(name)}-error`} className="error-text" role="alert">
                Enter a whole number from 1 to {formatNumber(LIMIT_CEILINGS[name])}, or leave it empty.
              </span>
            )}
          </div>
        );
      })}
      <div>
        <button
          id={SAVE_ID}
          type="button"
          className="btn btn-primary"
          disabled={!valid || !changed}
          onClick={() => onSave(parsed as FolderLimits)}
        >
          Save
        </button>
      </div>
    </div>
  );
}

interface Loaded {
  saved: FolderLimits;
  inherited: FolderLimits;
  defaults: Record<FolderLimitName, number>;
}

/** Loads the platform's values (no org) or one organisation's, again after each save. */
function useLimits(orgId?: string) {
  const [attempt, setAttempt] = useState(0);
  const [value, setValue] = useState<Loaded | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load: Promise<Loaded> = orgId
      ? getOrgFolderUploadLimits(orgId).then((reply) => ({ saved: reply.own, inherited: reply.platform, defaults: reply.defaults }))
      : getFolderUploadLimits().then(({ defaults, ...saved }) => ({ saved, inherited: saved, defaults }));
    load.then(
      (result) => !cancelled && setValue(result),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [orgId, attempt]);
  const failed = error?.attempt === attempt ? error.message : null;
  return { value, failed, reload: () => setAttempt((n) => n + 1) };
}

export function FolderUploadLimitsView() {
  const { value, failed, reload } = useLimits();
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  return (
    <section className={admin.section} aria-labelledby="folder-limits-heading">
      <h2 id="folder-limits-heading" className={admin.sectionHeading}>
        Folder uploads
      </h2>
      <p className={styles.summary}>{EXPLAIN} An organisation&apos;s own limits, set from its Manage menu, come first.</p>
      {failed && !value && <LoadFailed what="the folder upload limits" message={failed} onRetry={reload} />}
      {!value && !failed && (
        <p className="visually-hidden" role="status">
          Loading the folder upload limits…
        </p>
      )}
      {value && (
        <FolderLimitsForm
          key={JSON.stringify(value.saved)}
          {...value}
          org={false}
          onSave={(next) => setChanging(limitsConfirm(next, value.saved))}
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

/** The Manage menu's "Folder upload limits" for one organisation: super admin only. */
export function OrgFolderUploadLimitsDialog({ org, onClose }: { org: Organisation; onClose(): void }) {
  const { value, failed, reload } = useLimits(org.id);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [done, setDone] = useState("");
  return (
    <>
      <ActionDialog title={`Folder upload limits for ${org.name}`} onClose={onClose}>
        <div className={styles.dialogForm}>
          <p className={styles.summary}>{EXPLAIN} This organisation only.</p>
          <p className="visually-hidden" role="status">
            {done || (!value && !failed ? "Loading the folder upload limits…" : "")}
          </p>
          {failed && !value && <LoadFailed what="the folder upload limits" message={failed} onRetry={reload} />}
          {value && (
            <FolderLimitsForm
              key={JSON.stringify(value.saved)}
              {...value}
              org
              onSave={(next) => setChanging(limitsConfirm(next, value.saved, org))}
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
            reload();
          }}
        />
      )}
    </>
  );
}
