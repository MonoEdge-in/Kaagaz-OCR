"use client";

// The upload's Department picker (File management F2): which department the next files go
// into, kept for next time in this browser. Shown only where no folder decides it (a
// folder being viewed wins, and inside a department there is no question). It sits beside
// "Read with" and looks like it (Documents.module.css).

import { useId } from "react";
import styles from "@/components/documents/Documents.module.css";
import type { Folder } from "@/lib/types";
import { FILES_TEXT } from "./words";

export function DepartmentPicker({
  departments,
  value,
  onChange,
}: {
  departments: readonly Folder[];
  value: string | null;
  onChange: (id: string | null) => void;
}) {
  const id = useId();
  if (departments.length === 0) return null;
  return (
    <div className={styles.reader}>
      <label htmlFor={id} className={styles.readerLabel}>
        {FILES_TEXT.departmentLabel}
      </label>
      <select
        id={id}
        className={`input ${styles.readerSelect}`}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
      >
        <option value="">{FILES_TEXT.noDepartment}</option>
        {departments.map((department) => (
          <option key={department.id} value={department.id}>
            {department.name}
          </option>
        ))}
      </select>
    </div>
  );
}
