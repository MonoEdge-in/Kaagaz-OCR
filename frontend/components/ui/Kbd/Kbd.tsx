// A keyboard key, as in "1 first reading" or "J K next / previous".

import type { ReactNode } from "react";
import styles from "./Kbd.module.css";

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className={styles.kbd}>{children}</kbd>;
}
