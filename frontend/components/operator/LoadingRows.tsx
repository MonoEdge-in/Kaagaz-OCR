// A loading operator tab (#468): skeleton rows after a short delay (.delayed), and what is
// loading for a screen reader, as Organisations shows.

import admin from "@/components/admin/Admin.module.css";

const ROWS = [0, 1, 2];

export function LoadingRows({ what }: { what: string }) {
  return (
    <>
      <p className="visually-hidden" role="status">
        Loading {what}…
      </p>
      <div className={`delayed ${admin.skeletons}`} aria-hidden="true">
        {ROWS.map((row) => (
          <span key={row} className={`skeleton ${admin.skeletonRow}`} />
        ))}
      </div>
    </>
  );
}
