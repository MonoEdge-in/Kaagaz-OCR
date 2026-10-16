// The lockup: "Kaagaz" in the display face with "by MonoEdge" under it in
// small mono capitals, beside the brand rule. A link home inside the app;
// plain text on the set-password screen.

import Link from "next/link";
import styles from "./Wordmark.module.css";

export function Wordmark({ href }: { href?: string }) {
  const className = `${styles.wordmark} ${styles.rule}`;
  const content = (
    <>
      <span className={styles.name}>Kaagaz</span>{" "}
      <span className={styles.by}>by MonoEdge</span>
    </>
  );
  if (href) {
    return <Link href={href} className={className}>{content}</Link>;
  }
  return <span className={className}>{content}</span>;
}
