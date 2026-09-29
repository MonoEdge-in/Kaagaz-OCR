import { AdminView } from "@/components/admin/AdminView";
import { TwoStepPanel } from "./TwoStepPanel";

export default function AdminPage() {
  return <AdminView twoStep={<TwoStepPanel />} />;
}
