"use client";

// Make it a separate file (#521): "Make version {n} a separate file", its name filled in (its own
// upload name, else the family's with "(version n)"), what it keeps, and Make separate file. The
// name is checked as typed (empty, hidden or bidi characters, length); the server also refuses
// a name already used in the folder, and its words are shown. Done, the caller's toast says so,
// with Undo (back in the family, with its old name).

import { useId, useState } from "react";
import type { FormEvent } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import library from "@/components/documents/Library.module.css";
import { ApiError, separateVersion, undoSeparate } from "@/lib/api";
import type { DocumentVersion } from "@/lib/types";
import type { Chosen } from "./VersionChoice";
import { VERSION_TEXT, separateDefaultName, separateNameProblem } from "./words";

export function SeparateDialog({
  version,
  familyName,
  onDone,
  onClose,
}: {
  version: DocumentVersion;
  familyName: string;
  onDone(done: Chosen): void;
  onClose(): void;
}) {
  const id = useId();
  const ownName = version.display_name ?? "";
  const [name, setName] = useState(() => separateDefaultName(ownName, familyName, version.version_no));
  const [tried, setTried] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problem = tried ? separateNameProblem(name) : null;
  const shown = problem ?? refused;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTried(true);
    setRefused(null);
    if (separateNameProblem(name) !== null) return;
    const chosen = name.trim();
    setBusy(true);
    try {
      const { family_id: family } = await separateVersion(version.id, chosen);
      onDone({
        message: VERSION_TEXT.separated(version.version_no, chosen),
        undo: () => undoSeparate(version.id, family, ownName || undefined),
      });
    } catch (caught) {
      setRefused(caught instanceof ApiError ? caught.message : "Check your connection, then try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ActionDialog title={VERSION_TEXT.separateTitle(version.version_no)} onClose={onClose} canClose={() => !busy}>
      <form className={library.dialogForm} onSubmit={(event) => void submit(event)} noValidate>
        <div className="field">
          <label htmlFor={id}>{VERSION_TEXT.nameLabel}</label>
          <input
            id={id}
            className="input"
            value={name}
            autoComplete="off"
            aria-invalid={shown ? true : undefined}
            aria-describedby={shown ? `${id}-error` : `${id}-note`}
            onChange={(event) => {
              setName(event.target.value);
              setRefused(null);
            }}
            data-autofocus
          />
          {shown && (
            <p id={`${id}-error`} className="error-text" role="alert">
              {shown}
            </p>
          )}
        </div>
        <p id={`${id}-note`} className="muted">
          {VERSION_TEXT.separateNote(familyName)}
        </p>
        <div className={library.formButtons}>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {VERSION_TEXT.separateButton}
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
