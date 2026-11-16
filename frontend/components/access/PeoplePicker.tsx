"use client";

// Choosing people (PRD 7 PR 11): a "Find a person" box over a checkbox list of the organisation's
// active people (GET /people, read each time it opens; never kept). At most `cap` may be chosen:
// then the other boxes can't be ticked and a line says so. `exclude`: people already there.
// `scroll`: on the page itself (a department's people), the list keeps a height and scrolls in
// itself; in a dialog it grows, and the dialog's body scrolls.

import { useEffect, useId, useState } from "react";
import { listPeople } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { Person } from "@/lib/types";
import { CHOOSE_PEOPLE_CAP, NO_ONE_MATCHES, chosenCount } from "./words";
import styles from "./Access.module.css";

/** The people to list: not excluded, matching what was typed (any part of the name, any case). */
export function pickable(people: readonly Person[], find: string, exclude: readonly string[] = []): Person[] {
  const wanted = find.trim().toLocaleLowerCase();
  return people.filter((person) => !exclude.includes(person.id) && person.name.toLocaleLowerCase().includes(wanted));
}

interface PickerProps {
  legend: string;
  chosen: readonly Person[];
  onChange(chosen: Person[]): void;
  cap: number;
  exclude?: readonly string[];
  scroll?: boolean;
}

export function PeoplePicker(props: PickerProps) {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    listPeople().then(
      (reply) => {
        if (live) setPeople(reply.items);
      },
      (caught: unknown) => {
        if (live) setFailed(errorWords(caught));
      },
    );
    return () => {
      live = false;
    };
  }, []);
  return <PeopleList {...props} people={people} failed={failed} />;
}

/** The list itself, for the render tests: `people` null while they load. */
export function PeopleList({
  legend,
  people,
  failed = null,
  chosen,
  onChange,
  cap,
  exclude,
  scroll = false,
}: PickerProps & { people: readonly Person[] | null; failed?: string | null }) {
  const findId = useId();
  const [find, setFind] = useState("");
  const full = chosen.length >= cap;
  const picked = (person: Person) => chosen.some((each) => each.id === person.id);
  const shown = people ? pickable(people, find, exclude) : [];
  return (
    <fieldset className={styles.picker}>
      <legend>{legend}</legend>
      <label htmlFor={findId} className="visually-hidden">
        Find a person
      </label>
      <input
        id={findId}
        className="input"
        type="search"
        placeholder="Find a person"
        autoComplete="off"
        value={find}
        onChange={(event) => setFind(event.target.value)}
      />
      {failed && <p className="error-text">{failed}</p>}
      {!failed && people === null && (
        <p className="muted" role="status">
          Loading people…
        </p>
      )}
      {people && shown.length === 0 && <p className="muted">{NO_ONE_MATCHES}</p>}
      {shown.length > 0 && (
        <ul className={scroll ? `${styles.pickerList} ${styles.pickerScroll}` : styles.pickerList}>
          {shown.map((person) => (
            <li key={person.id}>
              <label className={styles.pickerRow}>
                <input
                  type="checkbox"
                  checked={picked(person)}
                  disabled={full && !picked(person)}
                  onChange={(event) =>
                    onChange(event.target.checked ? [...chosen, person] : chosen.filter((each) => each.id !== person.id))
                  }
                />
                {person.name}
              </label>
            </li>
          ))}
        </ul>
      )}
      <p className="hint" aria-live="polite">
        {chosen.length > 0 ? chosenCount(chosen.length) : ""}
        {full ? ` ${CHOOSE_PEOPLE_CAP(cap)}` : ""}
      </p>
    </fieldset>
  );
}
