"use client";

// The document page's Details (File management P1, O1 to O3): the owner and the label, each
// with a picker that saves at once for the whole family; then its department path, type,
// document date, author (version 1's uploader), last updated and who did it, and pages.
// The four labels come with their descriptions; a label informs and restricts nothing.

import { useEffect, useId, useState } from "react";
import { ApiError, listPeople, updateDocument } from "@/lib/api";
import { docTypeLabel, formatDate, formatDateTime } from "@/lib/format";
import type { DocumentDetail, Person, Sensitivity } from "@/lib/types";
import { LABELS, LABEL_DESCRIPTIONS, isLabel, labelName } from "./labels";
import { FILES_TEXT } from "./words";
import styles from "./Files.module.css";

export function DetailsPanel({
  detail,
  onSaved,
  people: given,
}: {
  detail: DocumentDetail;
  onSaved(message: string): void;
  people?: Person[]; // given by the render test; asked for otherwise
}) {
  const id = useId();
  const [people, setPeople] = useState<Person[] | null>(given ?? null);
  const [busy, setBusy] = useState<"owner" | "label" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editable = detail.deleted_at === null;

  useEffect(() => {
    if (given) return;
    let cancelled = false;
    listPeople().then(
      (reply) => !cancelled && setPeople(reply.items),
      () => !cancelled && setPeople([]),
    );
    return () => {
      cancelled = true;
    };
  }, [given]);

  async function save(what: "owner" | "label", change: { owner_id?: string; sensitivity?: Sensitivity }) {
    setBusy(what);
    setError(null);
    try {
      await updateDocument(detail.id, change);
      onSaved(FILES_TEXT.saved(what === "owner" ? FILES_TEXT.owner : FILES_TEXT.label));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Check your connection, then try again.");
    } finally {
      setBusy(null);
    }
  }

  const label = detail.sensitivity ?? "internal";
  const owner = detail.owner ?? null;
  const ownerChoices =
    people && owner && !people.some((person) => person.id === owner.id)
      ? [{ id: owner.id, name: owner.name ?? FILES_TEXT.noOwner }, ...people]
      : (people ?? []);
  const path = detail.folder ? [...detail.folder.path.map((folder) => folder.name)] : [];
  if (detail.folder && path.at(-1) !== detail.folder.name) path.push(detail.folder.name);
  const updatedAt = detail.updated_at ?? detail.created_at;
  const updatedBy = detail.updated_by ?? detail.uploaded_by_name;

  return (
    <section aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`} className="visually-hidden">
        {FILES_TEXT.details}
      </h2>
      <dl className={styles.details}>
        <dt>
          <label htmlFor={`${id}-owner`}>{FILES_TEXT.owner}</label>
        </dt>
        <dd className={styles.picker}>
          <select
            id={`${id}-owner`}
            className="input"
            value={owner?.id ?? ""}
            disabled={!editable || people === null || busy !== null}
            onChange={(event) => void save("owner", { owner_id: event.target.value })}
          >
            {owner === null && <option value="">{FILES_TEXT.noOwner}</option>}
            {people === null && owner !== null && <option value={owner.id}>{owner.name ?? FILES_TEXT.noOwner}</option>}
            {ownerChoices.map((person) => (
              <option key={person.id} value={person.id}>
                {person.name}
              </option>
            ))}
          </select>
          {busy === "owner" && <span className="muted">{FILES_TEXT.saving}</span>}
        </dd>

        <dt>
          <label htmlFor={`${id}-label`}>{FILES_TEXT.label}</label>
        </dt>
        <dd className={styles.picker}>
          <select
            id={`${id}-label`}
            className="input"
            value={label}
            disabled={!editable || busy !== null}
            aria-describedby={`${id}-label-help`}
            onChange={(event) => {
              if (isLabel(event.target.value)) void save("label", { sensitivity: event.target.value });
            }}
          >
            {LABELS.map((each) => (
              <option key={each} value={each}>
                {labelName(each)}
              </option>
            ))}
          </select>
          {busy === "label" && <span className="muted">{FILES_TEXT.saving}</span>}
          <p id={`${id}-label-help`} className={styles.labelHelp}>
            {LABEL_DESCRIPTIONS[label]}
          </p>
        </dd>

        <dt>{FILES_TEXT.departmentPath}</dt>
        <dd>{path.length > 0 ? path.join(" / ") : FILES_TEXT.notFiled}</dd>

        <dt>{FILES_TEXT.type}</dt>
        <dd>{docTypeLabel(detail.doc_type)}</dd>

        <dt>{FILES_TEXT.documentDate}</dt>
        <dd>{detail.document_date ? formatDate(detail.document_date) : FILES_TEXT.noDate}</dd>

        <dt>{FILES_TEXT.author}</dt>
        <dd>{detail.author_name ?? detail.uploaded_by_name ?? "—"}</dd>

        <dt>{FILES_TEXT.lastUpdated}</dt>
        <dd>
          <time dateTime={updatedAt}>{formatDateTime(updatedAt)}</time>
          {updatedBy ? ` · ${updatedBy}` : ""}
        </dd>

        <dt>{FILES_TEXT.pages}</dt>
        <dd className="tabular">{detail.page_count ?? "—"}</dd>
      </dl>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
