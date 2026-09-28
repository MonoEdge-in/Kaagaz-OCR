"use client";

// Holds the theme choice for the open page. It starts from what the server
// read in the cookie (app/layout.tsx), so the first render matches the HTML
// and nothing flickers or warns during hydration.

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { applyTheme } from "@/lib/theme";
import type { ThemeChoice } from "@/lib/theme";

interface ThemeState {
  choice: ThemeChoice;
  setChoice(choice: ThemeChoice): void;
}

const ThemeContext = createContext<ThemeState | null>(null);

export function ThemeProvider({ initial, children }: { initial: ThemeChoice; children: ReactNode }) {
  const [choice, setState] = useState<ThemeChoice>(initial);
  const setChoice = useCallback((next: ThemeChoice) => {
    applyTheme(next);
    setState(next);
  }, []);
  const value = useMemo(() => ({ choice, setChoice }), [choice, setChoice]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const state = useContext(ThemeContext);
  if (!state) throw new Error("useTheme must be used inside ThemeProvider");
  return state;
}
