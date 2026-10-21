// The lockup (the handoff's .k-brand): the folded-page mark, "Kaagaz" in the
// display face set wide, "by MonoEdge" under it in mono capitals. A link home
// inside the app; plain text on the set-password screen.

import Link from "next/link";
import styles from "./Wordmark.module.css";

export function Wordmark({ href }: { href?: string }) {
  const className = styles.wordmark;
  const content = (
    <>
      <span className={styles.mark} aria-hidden="true" />
      <span className={styles.words}>
        <span className={styles.name}>Kaagaz</span>{" "}
        <span className={styles.by}>by MonoEdge</span>
      </span>
    </>
  );
  if (href) {
    return <Link href={href} className={className}>{content}</Link>;
  }
  return <span className={className}>{content}</span>;
}
