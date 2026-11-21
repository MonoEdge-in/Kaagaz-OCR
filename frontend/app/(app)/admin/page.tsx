import { AdminView } from "@/components/admin/AdminView";

export const metadata = { title: "Admin" };

// ?tab=departments opens that tab (PRD 7 lead decision 12: Home's one-time card, the Access page's
// Back to Users).
export default async function AdminPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { tab, reauth } = await searchParams;
  return (
    <AdminView
      tab={typeof tab === "string" ? tab : undefined}
      reauth={typeof reauth === "string" ? reauth : undefined}
    />
  );
}
