"use client";

// Where files are dropped (desktop/Upload.html, D-12): a dashed area with the
// paper drawing, what to drop and the limits, the buttons (Choose files, Take a
// photo) and a side slot (the department). It lights up while files are
// dragged over it and hands the dropped files to the screen. Choosing files
// with the buttons is the screen's, with its own file input, so the drop zone
// is never the only way in.

import { useRef, useState } from "react";
import type { DragEvent, ReactNode } from "react";
import { cx } from "../cx";
import { Ledger } from "../EmptyState/EmptyState";
import { Icon } from "../Icon/Icon";
import styles from "./DropZone.module.css";

export interface DropZoneProps {
  /** "Drop up to 100 files here, or choose files". */
  title: ReactNode;
  /** The kinds and limits: "PDF, JPG, PNG, Excel, CSV · up to 50 MB each". */
  help: ReactNode;
  actions?: ReactNode;
  aside?: ReactNode;
  onFiles(files: File[]): void;
}

/** True when a drag carries files (not text or a link dragged from the page). */
export function carriesFiles(types: readonly string[]): boolean {
  return types.includes("Files");
}

export function DropZone({ title, help, actions, aside, onFiles }: DropZoneProps) {
  const [over, setOver] = useState(false);
  // dragenter and dragleave fire for every child; count them so the light stays on.
  const depth = useRef(0);

  const onDragEnter = (event: DragEvent) => {
    if (!carriesFiles(event.dataTransfer.types)) return;
    event.preventDefault();
    depth.current += 1;
    setOver(true);
  };
  const onDragOver = (event: DragEvent) => {
    if (!carriesFiles(event.dataTransfer.types)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };
  const onDragLeave = () => {
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setOver(false);
  };
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    depth.current = 0;
    setOver(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) onFiles(files);
  };

  return (
    <section
      className={cx(styles.drop, over && styles.over)}
      aria-label="Add files"
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div className={styles.art} aria-hidden="true">
        <Ledger className={styles.sheet} />
        <span className={styles.page}>
          <Icon name="upload" size="lg" />
        </span>
      </div>
      <div className={styles.text}>
        <p className="k-h3">{title}</p>
        <p className="k-small k-muted">{help}</p>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
      {aside ? <div className={styles.aside}>{aside}</div> : null}
    </section>
  );
}
