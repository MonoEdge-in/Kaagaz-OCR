// The lockup: "Kaagaz" in the display face with "by MonoEdge" under it in
// small mono capitals, beside the brand rule. A link home inside the app;
// plain text on the set-password screen.

import Link from "next/link";
import styles from "./Wordmark.module.css";

export function Wordmark({
  href,
  reader = true,
}: {
  href?: string;
  reader?: boolean;
}) {
  const className = `${styles.wordmark} ${styles.rule}`;
  const content = (
    <>
      <span className={styles.name}>Kaagaz</span>{" "}
      <span className={styles.by}>by MonoEdge</span>
      {reader && (
        <span className={styles.reader} aria-hidden="true">
          <span className={styles.robotHead}>
            <span className={styles.robotEye} />
          </span>
          <span className={styles.robotBody}>
            <span className={styles.robotArm} />
          </span>
          <span className={styles.document}>
            <span />
            <span />
            <span />
          </span>
        </span>
      )}
    </>
  );
  if (href) {
    return <Link href={href} className={className}>{content}</Link>;
  }
  return <span className={className}>{content}</span>;
}
