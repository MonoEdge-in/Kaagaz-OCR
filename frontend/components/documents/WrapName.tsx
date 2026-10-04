import { Fragment } from "react";
import { nameParts } from "./names";

/** A name that wraps only after its _ . - and @ marks (names.ts), shown in full. */
export function WrapName({ name }: { name: string }) {
  return (
    <>
      {nameParts(name).map((part, index) => (
        <Fragment key={index}>
          {index > 0 && <wbr />}
          {part}
        </Fragment>
      ))}
    </>
  );
}
