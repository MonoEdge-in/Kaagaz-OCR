import { AdminView } from "@/components/admin/AdminView";
import { TwoStepPanel } from "./TwoStepPanel";

export const metadata = { title: "Admin" };

// ?tab=departments opens that tab (PRD 7 lead decision 12: Home's one-time card, the Access page's
// Back to Users).
export default async function AdminPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { tab } = await searchParams;
  return <AdminView twoStep={<TwoStepPanel />} tab={typeof tab === "string" ? tab : undefined} />;
}
