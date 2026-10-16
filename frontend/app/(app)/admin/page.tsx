import { AdminView } from "@/components/admin/AdminView";
import { TwoStepPanel } from "./TwoStepPanel";

export const metadata = { title: "Admin" };

export default function AdminPage() {
  return <AdminView twoStep={<TwoStepPanel />} />;
}
