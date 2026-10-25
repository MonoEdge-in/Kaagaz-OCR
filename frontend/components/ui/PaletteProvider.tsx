"use client";

// Holds the colour palette choice for the open page, like ThemeProvider. It
// starts from what the server read in the cookie (app/layout.tsx), so the first
// render matches the HTML and nothing flickers or warns during hydration.

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { applyPalette } from "@/lib/palette";
import type { PaletteChoice } from "@/lib/palette";

interface PaletteState {
  palette: PaletteChoice;
  setPalette(choice: PaletteChoice): void;
}

const PaletteContext = createContext<PaletteState | null>(null);

export function PaletteProvider({ initial, children }: { initial: PaletteChoice; children: ReactNode }) {
  const [palette, setState] = useState<PaletteChoice>(initial);
  const setPalette = useCallback((next: PaletteChoice) => {
    applyPalette(next);
    setState(next);
  }, []);
  const value = useMemo(() => ({ palette, setPalette }), [palette, setPalette]);
  return <PaletteContext.Provider value={value}>{children}</PaletteContext.Provider>;
}

export function usePalette(): PaletteState {
  const state = useContext(PaletteContext);
  if (!state) throw new Error("usePalette must be used inside PaletteProvider");
  return state;
}
