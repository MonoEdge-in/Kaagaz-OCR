"use client";

// The shell's status bar (brief C): a 32 px row under <main> where a screen
// shows background work, such as a scan or an Ensemble check, instead of a
// modal. AppShell provides the row; a screen puts its line there with
// <InStatusBar>. The row takes its space only while something in it has
// content (UI audit G1, app/globals.css), so no page has an empty strip; a
// live region placed there while empty is still read out when words arrive.

import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

export const StatusBarSlot = createContext<HTMLElement | null>(null);

export function InStatusBar({ children }: { children: ReactNode }) {
  const slot = useContext(StatusBarSlot);
  return slot ? createPortal(children, slot) : null;
}
