// "Kaagaz" in the display face, beside the red margin rule of a ledger page.
// A link home inside the app; plain text on the sign-in page. The sign-in
// brand panel draws its own full-height rule, so there it goes without one.

import Link from "next/link";
import styles from "./Wordmark.module.css";

export function Wordmark({
  href,
  size = "normal",
  rule = true,
  reader = true,
}: {
  href?: string;
  size?: "normal" | "large";
  rule?: boolean;
  reader?: boolean;
}) {
  const className = [styles.wordmark, size === "large" && styles.large, rule && styles.rule]
    .filter(Boolean)
    .join(" ");
  const content = (
    <>
      Kaagaz
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
