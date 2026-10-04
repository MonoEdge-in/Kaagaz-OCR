// Where a file name may wrap. A long name such as "Ask_Brain_Eval_Report.pdf"
// has no spaces, so without help the browser breaks it anywhere ("...Report.p"
// / "df"). These pieces end after each _ . - and @, and the screen puts a
// <wbr> between them (WrapName.tsx), so a name wraps at those marks and is
// shown in full. Unit tested in tests/names.test.ts.

const BREAK_AFTER = new Set(["_", ".", "-", "@"]);

/** ".jpeg" and shorter stay on the piece before them: never "Report." / "pdf". */
const EXTENSION_MAX = 5;

/** The name in pieces that end after _ . - or @. Joined, they are the name again. */
export function nameParts(name: string): string[] {
  const dot = name.lastIndexOf(".");
  const extensionDot = dot > 0 && name.length - dot <= EXTENSION_MAX ? dot : -1;
  const parts: string[] = [];
  let start = 0;
  for (let i = 0; i < name.length - 1; i += 1) {
    const cut =
      BREAK_AFTER.has(name.charAt(i)) &&
      !BREAK_AFTER.has(name.charAt(i + 1)) &&
      i !== extensionDot &&
      [...name.slice(start, i)].some((char) => !BREAK_AFTER.has(char));
    if (cut) {
      parts.push(name.slice(start, i + 1));
      start = i + 1;
    }
  }
  parts.push(name.slice(start));
  return parts;
}
