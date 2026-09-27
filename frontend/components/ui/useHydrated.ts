"use client";

// False in the server HTML and during hydration, true once React runs in the
// browser. Used to keep the sign-in button disabled until its click handler is
// live, so an early press can never fall back to a plain form post. It reads
// an external "store" that never changes instead of setting state in an
// effect (react-hooks/set-state-in-effect).

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};
const onClient = () => true;
const onServer = () => false;

export function useHydrated(): boolean {
  return useSyncExternalStore(subscribe, onClient, onServer);
}
