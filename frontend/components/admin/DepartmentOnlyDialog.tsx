"use client";

// Department only, on or off (PRD 7 PR 11a, #604). It reads who would lose (and gain) the
// department's files first (GET .../department-only/preview), and its button waits for the names.
// On: who loses access, "Show all" for every name, and Add people to Finance. Off: how many files
// everyone will see. The change itself, and the password step when the admin gains the files, are
// the panel's (onConfirm), once this dialog has closed.

import { useEffect, useState } from "react";
import { includesMe } from "@/components/access/passwordStep";
import {
  ADD_PEOPLE,
  KEEP_TO_PEOPLE,
  OPEN_TO_EVERYONE,
  PREVIEW_FAILED,
  PREVIEW_LOADING,
  departmentOnlyText,
  keepQuestion,
  openQuestion,
  openText,
  showAll,
} from "@/components/access/words";
import access from "@/components/access/Access.module.css";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Icon } from "@/components/ui/Icon";
import { departmentOnlyPreview } from "@/lib/api";
import type { AdminDepartment, PlacePreview } from "@/lib/types";

interface Props {
  department: AdminDepartment;
  myId: string;
  onClose(): void;
  onAddPeople(): void;
  /** Switch it; `passwordFirst`: the admin is among those who gain the files. */
  onConfirm(passwordFirst: boolean): void;
}

export function DepartmentOnlyDialog(props: Props) {
  const { department } = props;
  const on = !department.department_only;
  const [attempt, setAttempt] = useState(0);
  const [preview, setPreview] = useState<PlacePreview | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    departmentOnlyPreview(department.id, on).then(
      (reply) => {
        if (live) setPreview(reply);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [department.id, on, attempt]);

  return (
    <DepartmentOnlyView
      {...props}
      preview={preview}
      failed={failed}
      onRetry={() => {
        setFailed(false);
        setAttempt((n) => n + 1);
      }}
    />
  );
}

/** The dialog, for the render tests: `preview` null while it loads. */
export function DepartmentOnlyView({
  department,
  myId,
  preview,
  failed,
  onRetry,
  onClose,
  onAddPeople,
  onConfirm,
}: Props & { preview: PlacePreview | null; failed: boolean; onRetry(): void }) {
  const on = !department.department_only;
  return (
    <ActionDialog title={on ? keepQuestion(department.name) : openQuestion(department.name)} onClose={onClose}>
      {failed ? (
        <div className="stack">
          <p className="error-text" role="alert">
            {PREVIEW_FAILED}
          </p>
          <div>
            <button type="button" className="btn" onClick={onRetry}>
              <Icon name="refresh" />
              Try again
            </button>
          </div>
        </div>
      ) : preview === null ? (
        <p className="muted" role="status">
          {PREVIEW_LOADING}
        </p>
      ) : on ? (
        <>
          <p>{departmentOnlyText(department.name, preview.losing, department.members)}</p>
          {preview.losing.length > 3 && (
            <details>
              <summary>{showAll(preview.losing.length)}</summary>
              <ul className={access.names}>
                {preview.losing.map((person) => (
                  <li key={person.id}>{person.name}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      ) : (
        <p>{openText(department.name, preview.files)}</p>
      )}
      {/* [Cancel] then the action; Cancel has focus, so Enter never acts by accident. */}
      <div className="actions">
        <button type="button" className="btn" data-autofocus onClick={onClose}>
          Cancel
        </button>
        {on && (
          <button type="button" className="btn" onClick={onAddPeople}>
            {`${ADD_PEOPLE} to ${department.name}`}
          </button>
        )}
        <button
          type="button"
          className={`btn ${on ? "btn-danger" : "btn-primary"}`}
          disabled={preview === null}
          onClick={() => preview && onConfirm(includesMe(preview.gaining, myId))}
        >
          {on ? KEEP_TO_PEOPLE : OPEN_TO_EVERYONE}
        </button>
      </div>
    </ActionDialog>
  );
}
