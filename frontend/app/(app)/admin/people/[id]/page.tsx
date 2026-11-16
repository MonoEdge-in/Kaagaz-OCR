// /admin/people/{id}: one person's Access page (PRD 7 PR 11a, #604). The tab's title never names
// the person (browser history syncs off the device).

import { AccessPage } from "@/components/admin/AccessPage";

export const metadata = { title: "Admin" };

export default async function PersonAccessPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AccessPage key={id} userId={id} />;
}
