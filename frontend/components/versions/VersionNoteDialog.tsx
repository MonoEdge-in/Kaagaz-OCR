"use client";

// "Upload new version" asks for a note first (File management V9): optional, at most 200
// characters, such as "Mutation 1234 added". Upload sends the chosen file with it.

import { useId, useState } from "react";
import type { FormEvent } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import library from "@/components/documents/Library.module.css";
import { NOTE_MAX, VERSION_TEXT, cleanNote } from "./words";

export function VersionNoteDialog({
  documentName,
  fileName,
  onUpload,
  onClose,
}: {
  documentName: string;
  fileName: string;
  onUpload(note: string | undefined): void;
  onClose(): void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const { note, problem } = cleanNote(text);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (problem) return;
    onUpload(note ?? undefined);
  }
  return (
    <ActionDialog title={`New version of ${documentName}`} onClose={onClose}>
      <form className={library.dialogForm} onSubmit={submit} noValidate>
        <p className="muted">{fileName}</p>
        <div className="field">
          <label htmlFor={id}>{VERSION_TEXT.noteLabel}</label>
          <input
            id={id}
            className="input"
            value={text}
            maxLength={NOTE_MAX + 20}
            placeholder={VERSION_TEXT.notePlaceholder}
            autoComplete="off"
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${id}-error` : undefined}
            onChange={(event) => setText(event.target.value)}
            data-autofocus
          />
          {problem && (
            <p id={`${id}-error`} className="error-text" role="alert">
              {problem}
            </p>
          )}
        </div>
        <div className={library.formButtons}>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary">
            Upload
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
