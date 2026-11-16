// /access-activity: who changed who can see what (PRD 7 PR 11c, #604), for Management and admins.
// The server refuses anyone else; the nav link shows only for Management (components/shell/nav.ts).

import { AccessActivity } from "@/components/activity/AccessActivity";

export const metadata = { title: "Access activity" };

export default function AccessActivityPage() {
  return <AccessActivity />;
}
