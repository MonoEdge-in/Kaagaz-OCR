"use client";

// The filter chips above the list: Tags (every tag chosen must match), Type
// and Date uploaded, and Needs attention (PRD 5 U2), a toggle (aria-pressed)
// that keeps only documents with reading problems. Each chip says what it is set to and opens its panel just
// below the row, one panel at a time. A change goes into the page address at
// once, and the list follows it. In a folder with subfolders, a checkbox also
// takes in what is inside them. UI audit D7 (16 Oct 2026): a chip that opens a panel
// has a chevron; a set chip is filled, says what it is set to, and has its own x to
// remove it; "Clear all (N)" counts the filters set.
// File management Q3, Q5: Department, Type, Owner, Label, Party, Property, Document date and
// Has newer version come first, in that order; they narrow the list and the search alike.

import { useId, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import {
  FILTER_TEXT,
  departmentChipText,
  documentDateChipText,
  labelsChipText,
  ownerChipText,
  partyChipText,
  propertyChipText,
} from "@/components/files/filterChips";
import { LABELS, LABEL_DESCRIPTIONS, labelName } from "@/components/files/labels";
import { IconButton } from "@/components/ui/IconButton";
import { typeLabel } from "@/lib/format";
import type { Folder, Person, Sensitivity, Tag } from "@/lib/types";
import { dateChipText, tagsChipText, typeChipText } from "./library";
import {
  FILTER_TYPES,
  MAX_PARTY_LENGTH,
  MAX_PROPERTY_LENGTH,
  NO_FILTERS,
  filterCount,
  isIsoDate,
} from "./libraryQuery";
import type { LibraryState } from "./libraryQuery";
import styles from "./Library.module.css";

type Panel = "department" | "type" | "owner" | "label" | "party" | "property" | "docDate" | "tags" | "date";

export interface LibraryFiltersProps {
  library: LibraryState;
  tags: Tag[] | null; // null until loaded
  tagsError: string | null;
  onRetryTags(): void;
  showSubfolders: boolean;
  onChange(next: LibraryState): void;
  departments?: readonly Folder[]; // File management: the departments, in their order
  people?: readonly Person[] | null; // null until loaded
}

export function LibraryFilters({
  library,
  tags,
  tagsError,
  onRetryTags,
  showSubfolders,
  onChange,
  departments = [],
  people = null,
}: LibraryFiltersProps) {
  const [open, setOpen] = useState<Panel | null>(null);
  const id = useId();
  const panelId = `${id}-panel`;
  const rowRef = useRef<HTMLDivElement>(null);
  const setCount = filterCount(library);
  const tagNames = library.tagIds.map((tagId) =>
    tags === null ? "…" : (tags.find((tag) => tag.id === tagId)?.name ?? "a tag no longer there"),
  );
  const departmentName =
    library.departmentId === null
      ? null
      : (departments.find((folder) => folder.id === library.departmentId)?.name ?? "…");
  const ownerName =
    library.ownerId === null ? null : (people?.find((person) => person.id === library.ownerId)?.name ?? "…");

  const chip = (panel: Panel, words: string, active: boolean, remove: Partial<LibraryState>) => (
    <span className={styles.chipGroup} data-active={active ? "true" : undefined}>
      <button
        type="button"
        className={`${styles.chip} ${styles.chipOpens}`}
        data-active={active ? "true" : undefined}
        aria-expanded={open === panel}
        aria-controls={open === panel ? panelId : undefined}
        onClick={() => setOpen(open === panel ? null : panel)}
      >
        {words}
      </button>
      {active && (
        <IconButton
          icon="close"
          label={`Remove filter: ${words}`}
          className={styles.chipRemove}
          onClick={() => {
            if (open === panel) setOpen(null);
            onChange({ ...library, ...remove });
          }}
        />
      )}
    </span>
  );

  function toggleTag(tagId: string) {
    const tagIds = library.tagIds.includes(tagId)
      ? library.tagIds.filter((chosen) => chosen !== tagId)
      : [...library.tagIds, tagId];
    onChange({ ...library, tagIds });
  }

  function toggleLabel(label: Sensitivity) {
    const labels = library.labels.includes(label)
      ? library.labels.filter((chosen) => chosen !== label)
      : LABELS.filter((each) => each === label || library.labels.includes(each));
    onChange({ ...library, labels });
  }

  // Esc in an open panel closes it and goes back to the chip that opened it.
  function closeOnEscape(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape" || open === null) return;
    rowRef.current?.querySelector<HTMLElement>('[aria-expanded="true"]')?.focus();
    setOpen(null);
  }

  function clear() {
    setOpen(null);
    onChange({ ...library, ...NO_FILTERS });
  }

  return (
    <div className={styles.filters} onKeyDown={closeOnEscape}>
      <div ref={rowRef} className={styles.filterRow} role="group" aria-label="Filters">
        {!library.trash &&
          departments.length > 0 &&
          chip("department", departmentChipText(departmentName), library.departmentId !== null, {
            departmentId: null,
          })}
        {chip("type", typeChipText(library.type), library.type !== null, { type: null })}
        {!library.trash && (
          <>
            {chip("owner", ownerChipText(ownerName), library.ownerId !== null, { ownerId: null })}
            {chip("label", labelsChipText(library.labels), library.labels.length > 0, { labels: [] })}
            {chip("party", partyChipText(library.party), library.party !== null, { party: null })}
            {chip("property", propertyChipText(library.property), library.property !== null, { property: null })}
            {chip(
              "docDate",
              documentDateChipText(library.docFrom, library.docTo),
              library.docFrom !== null || library.docTo !== null,
              { docFrom: null, docTo: null },
            )}
            <button
              type="button"
              className={styles.chip}
              data-active={library.newer ? "true" : undefined}
              aria-pressed={library.newer}
              onClick={() => onChange({ ...library, newer: !library.newer })}
            >
              {FILTER_TEXT.newer}
            </button>
          </>
        )}
        {chip("tags", tagsChipText(tagNames), library.tagIds.length > 0, { tagIds: [] })}
        {chip("date", dateChipText(library.from, library.to), library.from !== null || library.to !== null, {
          from: null,
          to: null,
        })}
        {!library.trash && (
          <button
            type="button"
            className={styles.chip}
            data-active={library.attention ? "true" : undefined}
            aria-pressed={library.attention}
            onClick={() => onChange({ ...library, attention: !library.attention })}
          >
            Needs attention
          </button>
        )}
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
        {setCount > 0 && (
          <button type="button" className="btn btn-quiet" onClick={clear}>
            Clear all <span className="tabular">({setCount})</span>
          </button>
        )}
      </div>

      {open === "department" && (
        <div id={panelId} className={`motion-fade ${styles.filterPanel}`}>
          <fieldset>
            <legend>{FILTER_TEXT.departmentLegend}</legend>
            <ul className={styles.choices}>
              <li>
                <label className={styles.check}>
                  <input
                    type="radio"
                    name={`${id}-department`}
                    checked={library.departmentId === null}
                    onChange={() => onChange({ ...library, departmentId: null })}
                  />
                  {FILTER_TEXT.anyDepartment}
                </label>
              </li>
              {departments.map((department) => (
                <li key={department.id}>
                  <label className={styles.check}>
                    <input
                      type="radio"
                      name={`${id}-department`}
                      checked={library.departmentId === department.id}
                      onChange={() => onChange({ ...library, departmentId: department.id })}
                    />
                    {department.name}
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        </div>
      )}

      {open === "owner" && (
        <div id={panelId} className={`motion-fade ${styles.filterPanel}`}>
          <fieldset>
            <legend>{FILTER_TEXT.ownerLegend}</legend>
            {people === null ? (
              <p className="muted" role="status">
                {FILTER_TEXT.loadingPeople}
              </p>
            ) : (
              <ul className={styles.choices}>
                <li>
                  <label className={styles.check}>
                    <input
                      type="radio"
                      name={`${id}-owner`}
                      checked={library.ownerId === null}
                      onChange={() => onChange({ ...library, ownerId: null })}
                    />
                    {FILTER_TEXT.anyone}
                  </label>
                </li>
                {people.map((person) => (
                  <li key={person.id}>
                    <label className={styles.check}>
                      <input
                        type="radio"
                        name={`${id}-owner`}
                        checked={library.ownerId === person.id}
                        onChange={() => onChange({ ...library, ownerId: person.id })}
                      />
                      {person.name}
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </fieldset>
        </div>
      )}

      {open === "label" && (
        <div id={panelId} className={`motion-fade ${styles.filterPanel}`}>
          <fieldset>
            <legend>{FILTER_TEXT.labelLegend}</legend>
            <ul className={styles.choices}>
              {LABELS.map((label) => (
                <li key={label}>
                  <label className={styles.check} title={LABEL_DESCRIPTIONS[label]}>
                    <input
                      type="checkbox"
                      checked={library.labels.includes(label)}
                      onChange={() => toggleLabel(label)}
                    />
                    {labelName(label)}
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        </div>
      )}

      {open === "party" && (
        <div id={panelId} className={`motion-fade ${styles.filterPanel}`}>
          <WordsFilter
            legend={FILTER_TEXT.partyLegend}
            hint={FILTER_TEXT.partyHint}
            value={library.party}
            max={MAX_PARTY_LENGTH}
            onApply={(party) => onChange({ ...library, party })}
          />
        </div>
      )}

      {open === "property" && (
        <div id={panelId} className={`motion-fade ${styles.filterPanel}`}>
          <WordsFilter
            legend={FILTER_TEXT.propertyLegend}
            hint={FILTER_TEXT.propertyHint}
            value={library.property}
            max={MAX_PROPERTY_LENGTH}
            onApply={(property) => onChange({ ...library, property })}
          />
        </div>
      )}

      {open === "docDate" && (
        <div id={panelId} className={`motion-fade ${styles.filterPanel}`}>
          <DateRange
            legend={FILTER_TEXT.documentDateLegend}
            from={library.docFrom}
            to={library.docTo}
            onApply={(docFrom, docTo) => onChange({ ...library, docFrom, docTo })}
          />
        </div>
      )}

      {open === "tags" && (
        <div id={panelId} className={`motion-fade ${styles.filterPanel}`}>
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
        <div id={panelId} className={`motion-fade ${styles.filterPanel}`}>
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
        <div id={panelId} className={`motion-fade ${styles.filterPanel}`}>
          <DateRange
            legend="Show documents uploaded"
            from={library.from}
            to={library.to}
            onApply={(from, to) => onChange({ ...library, from, to })}
          />
        </div>
      )}
    </div>
  );
}

/** One line of words (a party, a survey number), applied on Enter or Apply; empty removes it. */
function WordsFilter({
  legend,
  hint,
  value,
  max,
  onApply,
}: {
  legend: string;
  hint: string;
  value: string | null;
  max: number;
  onApply(next: string | null): void;
}) {
  const id = useId();
  const [text, setText] = useState(value ?? "");
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const clean = text.trim().replace(/\s+/g, " ");
    onApply(clean === "" ? null : clean);
  }
  return (
    <form onSubmit={submit}>
      <fieldset>
        <legend>{legend}</legend>
        <div className={styles.dates}>
          <div className="field">
            <label htmlFor={id}>{hint}</label>
            <input
              id={id}
              className="input"
              value={text}
              maxLength={max}
              autoComplete="off"
              onChange={(event) => setText(event.target.value)}
            />
          </div>
          <button type="submit" className={`btn ${styles.applyButton}`}>
            {FILTER_TEXT.apply}
          </button>
        </div>
      </fieldset>
    </form>
  );
}

/** Two dates, both days included. A range that ends before it starts is not applied. */
function DateRange({
  legend,
  from: startFrom,
  to: startTo,
  onApply,
}: {
  legend: string;
  from: string | null;
  to: string | null;
  onApply(from: string | null, to: string | null): void;
}) {
  const [from, setFrom] = useState(startFrom ?? "");
  const [to, setTo] = useState(startTo ?? "");
  const id = useId();
  const errorId = `${id}-error`;
  const backwards = from !== "" && to !== "" && from > to;

  function apply(nextFrom: string, nextTo: string) {
    setFrom(nextFrom);
    setTo(nextTo);
    if (nextFrom !== "" && nextTo !== "" && nextFrom > nextTo) return;
    onApply(isIsoDate(nextFrom) ? nextFrom : null, isIsoDate(nextTo) ? nextTo : null);
  }

  const invalid = backwards ? { "aria-invalid": true, "aria-describedby": errorId } : {};
  return (
    <fieldset>
      <legend>{legend}</legend>
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
