// A failed load on the operator screens: what failed, why, and Try again.

import { Icon } from "@/components/ui/Icon";
import admin from "@/components/admin/Admin.module.css";

export function LoadFailed({ what, message, onRetry }: { what: string; message: string; onRetry(): void }) {
  return (
    <div className={admin.errorBanner} role="alert">
      <Icon name="alert" className={admin.errorIcon} />
      <div className={admin.errorBody}>
        <p className={admin.errorTitle}>Could not load {what}.</p>
        <p>{message}</p>
      </div>
      <button type="button" className="btn" onClick={onRetry}>
        <Icon name="refresh" />
        Try again
      </button>
    </div>
  );
}
