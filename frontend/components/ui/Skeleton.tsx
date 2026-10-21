// A grey block where content is loading (.skeleton in app/globals.css: an
// opacity pulse only, stopped by reduced motion). The screen gives it the
// final content's size through className, so nothing moves when it arrives.
// Hidden from screen readers: the screen says "Loading…" once, in its own words.

export function Skeleton({ className }: { className?: string }) {
  return <span className={className ? `skeleton ${className}` : "skeleton"} aria-hidden="true" />;
}
