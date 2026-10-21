// The UI kit on one page, laid out like design/kaagaz-ui-handoff/screens/system/
// Components.html, to check the kit against it in light and dark
// (/kit?theme=dark). Development only: a production build answers 404.

import { notFound } from "next/navigation";
import { KitSheet } from "./KitSheet";

export const metadata = { title: "Components" };

export default async function KitPage({ searchParams }: { searchParams: Promise<{ theme?: string | string[] }> }) {
  if (process.env.NODE_ENV === "production") notFound();
  const theme = (await searchParams).theme === "dark" ? "dark" : "light";
  return <KitSheet theme={theme} />;
}
