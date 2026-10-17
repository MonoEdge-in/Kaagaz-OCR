// /settings/ai: a member's own AI settings, from the account menu (spec 17 Oct 2026).

import { AiSettingsPanel } from "@/components/admin/AiSettingsPanel";

export const metadata = { title: "AI settings" };

export default function AiSettingsPage() {
  return (
    <div className="page">
      <header className="page-header">
        <h1>AI settings</h1>
      </header>
      <AiSettingsPanel scope="member" />
    </div>
  );
}
