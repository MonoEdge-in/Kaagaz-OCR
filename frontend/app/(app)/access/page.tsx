// /access: every person and their access, for admins (#706). The server refuses anyone else; the
// nav link shows only for an admin session (components/shell/nav.ts).

import { AccessList } from "@/components/admin/AccessList";

export const metadata = { title: "Access" };

export default function AccessListPage() {
  return <AccessList />;
}
