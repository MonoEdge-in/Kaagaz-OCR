"use client";

// Give access (PRD 7 PR 11b, #604): an Admin lets named people open one file or many, with every
// version, now and later. One call (POST /documents/shares, at most 100 files and 20 people,
// checked here first); my own name among the people asks for my password first. Cancel first and
// focused; a refusal (in Trash, a disabled account) stays in the dialog in its own words.

import { useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { giveFiles } from "@/lib/api";
import type { Person } from "@/lib/types";
import { includesMe } from "./passwordStep";
import { PeoplePicker } from "./PeoplePicker";
import { usePasswordStep } from "./SelfPasswordDialog";
import {
  CHOOSE_FILES_CAP,
  CHOOSE_PEOPLE_CAP,
  GIVE_TEXT,
  MAX_GIVE_FILES,
  MAX_GIVE_PEOPLE,
  accessErrorText,
  filesWhat,
  giveTitle,
  sharedToast,
} from "./words";

type GivenFile = { id: string; display_name: string };

/** What stops the give before it is sent: too many files or people; null when it may go. */
export function giveProblem(files: number, people: number): string | null {
  if (files > MAX_GIVE_FILES) return CHOOSE_FILES_CAP;
  if (people > MAX_GIVE_PEOPLE) return CHOOSE_PEOPLE_CAP(MAX_GIVE_PEOPLE);
  return null;
}

export function GiveAccessDialog({
  files,
  myId,
  onClose,
  onClosed,
  onGiven,
}: {
  files: readonly GivenFile[];
  myId: string;
  onClose(): void;
  onClosed?(): void;
  /** Given: the toast's words. */
  onGiven(message: string): void;
}) {
  const [chosen, setChosen] = useState<Person[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const passwordStep = usePasswordStep();
  const problem = giveProblem(files.length, chosen.length);

  async function give() {
    setBusy(true);
    setError(null);
    try {
      const reply = await passwordStep.run(
        filesWhat(files),
        (password) => giveFiles(files.map((file) => file.id), chosen.map((person) => person.id), password),
        includesMe(chosen, myId),
      );
      if (reply !== null) onGiven(sharedToast(files.length, chosen));
    } catch (caught) {
      setError(accessErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <ActionDialog title={giveTitle(files)} onClose={onClose} onClosed={onClosed} canClose={() => !busy}>
        <GiveAccessForm
          tooMany={files.length > MAX_GIVE_FILES}
          chosen={chosen}
          onChoose={setChosen}
          problem={problem}
          error={error}
          busy={busy}
          onCancel={onClose}
          onGive={() => void give()}
        />
      </ActionDialog>
      {passwordStep.dialog}
    </>
  );
}

/** The form, for the render tests. */
export function GiveAccessForm({
  tooMany,
  chosen,
  onChoose,
  problem,
  error,
  busy,
  onCancel,
  onGive,
}: {
  tooMany: boolean;
  chosen: readonly Person[];
  onChoose(chosen: Person[]): void;
  problem: string | null;
  error: string | null;
  busy: boolean;
  onCancel(): void;
  onGive(): void;
}) {
  return (
    <div className="stack">
      <p>{GIVE_TEXT}</p>
      {tooMany ? (
        <p className="error-text" role="alert">
          {CHOOSE_FILES_CAP}
        </p>
      ) : (
        <PeoplePicker legend="People" chosen={chosen} onChange={onChoose} cap={MAX_GIVE_PEOPLE} />
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button type="button" className="btn" data-autofocus onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || problem !== null || chosen.length === 0}
          aria-busy={busy || undefined}
          onClick={onGive}
        >
          {busy && <span className="spinner" aria-hidden="true" />}
          Give access
        </button>
      </div>
    </div>
  );
}
