"use client";

// Choosing people (PRD 7 PR 11): a "Find a person" box over a checkbox list of the organisation's
// active people (GET /people, read each time it opens; never kept). At most `cap` may be chosen:
// then the other boxes can't be ticked and a line says so. `exclude`: people already there.

import { useEffect, useId, useState } from "react";
import { listPeople } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { Person } from "@/lib/types";
import { CHOOSE_PEOPLE_CAP } from "./words";
import styles from "./Access.module.css";

/** The people to list: not excluded, matching what was typed (any part of the name, any case). */
export function pickable(people: readonly Person[], find: string, exclude: readonly string[] = []): Person[] {
  const wanted = find.trim().toLocaleLowerCase();
  return people.filter((person) => !exclude.includes(person.id) && person.name.toLocaleLowerCase().includes(wanted));
}

export function PeoplePicker({
  legend,
  chosen,
  onChange,
  cap,
  exclude,
}: {
  legend: string;
  chosen: readonly Person[];
  onChange(chosen: Person[]): void;
  cap: number;
  exclude?: readonly string[];
}) {
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
  return <PeopleList legend={legend} people={people} failed={failed} chosen={chosen} onChange={onChange} cap={cap} exclude={exclude} />;
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
}: {
  legend: string;
  people: readonly Person[] | null;
  failed?: string | null;
  chosen: readonly Person[];
  onChange(chosen: Person[]): void;
  cap: number;
  exclude?: readonly string[];
}) {
  const findId = useId();
  const [find, setFind] = useState("");
  const full = chosen.length >= cap;
  const picked = (person: Person) => chosen.some((each) => each.id === person.id);
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
      {people && (
        <ul className={styles.pickerList}>
          {pickable(people, find, exclude).map((person) => (
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
      {full && <p className="hint">{CHOOSE_PEOPLE_CAP(cap)}</p>}
    </fieldset>
  );
}
