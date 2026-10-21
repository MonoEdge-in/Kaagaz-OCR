// Joins class names, skipping the ones switched off: cx(styles.btn, on && styles.on).

export function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}
