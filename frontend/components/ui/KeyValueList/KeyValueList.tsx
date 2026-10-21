// A document's details as name and value, one rule under each pair: Type,
// Department, Document date. A value can carry its status badge or citation.

import { Fragment } from "react";
import type { ReactNode } from "react";
import styles from "./KeyValueList.module.css";

export interface KeyValue {
  key: string;
  name: ReactNode;
  value: ReactNode;
}

export function KeyValueList({ items }: { items: KeyValue[] }) {
  return (
    <dl className={styles.kv}>
      {items.map((item) => (
        <Fragment key={item.key}>
          <dt>{item.name}</dt>
          <dd>{item.value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}
