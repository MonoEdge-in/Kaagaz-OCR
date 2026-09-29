"use client";

// The filter chips above the list: Tags (every tag chosen must match), Type
// and Date uploaded. Each chip says what it is set to and opens its panel just
// below the row, one panel at a time. A change goes into the page address at
// once, and the list follows it. In a folder with subfolders, a checkbox also
// takes in what is inside them.

import { useId, useState } from "react";
import { typeLabel } from "@/lib/format";
import type { Tag } from "@/lib/types";
import { dateChipText, tagsChipText, typeChipText } from "./library";
import { FILTER_TYPES, isFiltered, isIsoDate } from "./libraryQuery";
import type { LibraryState } from "./libraryQuery";
import styles from "./Library.module.css";

type Panel = "tags" | "type" | "date";

export interface LibraryFiltersProps {
  library: LibraryState;
  tags: Tag[] | null; // null until loaded
  tagsError: string | null;
  onRetryTags(): void;
  showSubfolders: boolean;
  onChange(next: LibraryState): void;
}

export function LibraryFilters({
  library,
  tags,
  tagsError,
  onRetryTags,
  showSubfolders,
  onChange,
}: LibraryFiltersProps) {
  const [open, setOpen] = useState<Panel | null>(null);
  const id = useId();
  const panelId = `${id}-panel`;
  const filtered = isFiltered(library);
  const tagNames = library.tagIds.map((tagId) =>
    tags === null ? "…" : (tags.find((tag) => tag.id === tagId)?.name ?? "a tag no longer there"),
  );

  const chip = (panel: Panel, words: string, active: boolean) => (
    <button
      type="button"
      className={styles.chip}
      data-active={active ? "true" : undefined}
      aria-expanded={open === panel}
      aria-controls={open === panel ? panelId : undefined}
      onClick={() => setOpen(open === panel ? null : panel)}
    >
      {words}
    </button>
  );

  function toggleTag(tagId: string) {
    const tagIds = library.tagIds.includes(tagId)
      ? library.tagIds.filter((chosen) => chosen !== tagId)
      : [...library.tagIds, tagId];
    onChange({ ...library, tagIds });
  }

  function clear() {
    setOpen(null);
    onChange({ ...library, tagIds: [], type: null, from: null, to: null });
  }

  return (
    <div className={styles.filters}>
      <div className={styles.filterRow} role="group" aria-label="Filters">
        {chip("tags", tagsChipText(tagNames), library.tagIds.length > 0)}
        {chip("type", typeChipText(library.type), library.type !== null)}
        {chip("date", dateChipText(library.from, library.to), library.from !== null || library.to !== null)}
        {showSubfolders && (
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={library.subfolders}
              onChange={() => onChange({ ...library, subfolders: !library.subfolders })}
            />
            Include subfolders
          </label>
        )}
        {filtered && (
          <button type="button" className="btn btn-quiet" onClick={clear}>
            Clear filters
          </button>
        )}
      </div>

      {open === "tags" && (
        <div id={panelId} className={styles.filterPanel}>
          <fieldset>
            <legend>Show documents with all of these tags</legend>
            {tags === null && tagsError !== null ? (
              <div className={styles.note}>
                <p className="error-text" role="alert">
                  Could not load the tags. {tagsError}
                </p>
                <button type="button" className="btn" onClick={onRetryTags}>
                  Try again
                </button>
              </div>
            ) : tags === null ? (
              <p className="muted" role="status">
                Loading tags…
              </p>
            ) : tags.length === 0 ? (
              <p className="muted">No tags yet. Add tags to a document from its Actions button.</p>
            ) : (
              <ul className={styles.choices}>
                {tags.map((tag) => (
                  <li key={tag.id}>
                    <label className={styles.check}>
                      <input
                        type="checkbox"
                        checked={library.tagIds.includes(tag.id)}
                        onChange={() => toggleTag(tag.id)}
                      />
                      {tag.name}
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </fieldset>
        </div>
      )}

      {open === "type" && (
        <div id={panelId} className={styles.filterPanel}>
          <fieldset>
            <legend>Show documents of this type</legend>
            <ul className={styles.choices}>
              <li>
                <label className={styles.check}>
                  <input
                    type="radio"
                    name={`${id}-type`}
                    checked={library.type === null}
                    onChange={() => onChange({ ...library, type: null })}
                  />
                  Any type
                </label>
              </li>
              {FILTER_TYPES.map((type) => (
                <li key={type}>
                  <label className={styles.check}>
                    <input
                      type="radio"
                      name={`${id}-type`}
                      checked={library.type === type}
                      onChange={() => onChange({ ...library, type })}
                    />
                    {typeLabel(type)}
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        </div>
      )}

      {open === "date" && (
        <div id={panelId} className={styles.filterPanel}>
          <DateRange library={library} onChange={onChange} />
        </div>
      )}
    </div>
  );
}

/** Two dates, both days included. A range that ends before it starts is not applied. */
function DateRange({
  library,
  onChange,
}: {
  library: LibraryState;
  onChange(next: LibraryState): void;
}) {
  const [from, setFrom] = useState(library.from ?? "");
  const [to, setTo] = useState(library.to ?? "");
  const id = useId();
  const errorId = `${id}-error`;
  const backwards = from !== "" && to !== "" && from > to;

  function apply(nextFrom: string, nextTo: string) {
    setFrom(nextFrom);
    setTo(nextTo);
    if (nextFrom !== "" && nextTo !== "" && nextFrom > nextTo) return;
    onChange({
      ...library,
      from: isIsoDate(nextFrom) ? nextFrom : null,
      to: isIsoDate(nextTo) ? nextTo : null,
    });
  }

  const invalid = backwards ? { "aria-invalid": true, "aria-describedby": errorId } : {};
  return (
    <fieldset>
      <legend>Show documents uploaded</legend>
      <div className={styles.dates}>
        <div className="field">
          <label htmlFor={`${id}-from`}>From</label>
          <input
            id={`${id}-from`}
            className="input"
            type="date"
            value={from}
            onChange={(event) => apply(event.target.value, to)}
            {...invalid}
          />
        </div>
        <div className="field">
          <label htmlFor={`${id}-to`}>To (that day included)</label>
          <input
            id={`${id}-to`}
            className="input"
            type="date"
            value={to}
            onChange={(event) => apply(from, event.target.value)}
            {...invalid}
          />
        </div>
      </div>
      {backwards && (
        <p id={errorId} className="error-text" role="alert">
          The first day is after the last day. Change one of them.
        </p>
      )}
    </fieldset>
  );
}
