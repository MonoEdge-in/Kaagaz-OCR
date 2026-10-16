import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { ThemeProvider } from "@/components/ui/ThemeProvider";
import { parseTheme, THEME_COOKIE, themeAttribute } from "@/lib/theme";
import "./globals.css";

// Tab titles stay generic: browser history syncs off the device, so no
// document or customer name ever goes here. A section sets only its own
// fixed name ("Documents · Kaagaz").
export const metadata: Metadata = {
  title: { default: "Kaagaz", template: "%s · Kaagaz" },
  description: "Ask your business documents. Every answer shows its source.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f2e9" },
    { media: "(prefers-color-scheme: dark)", color: "#14161b" },
  ],
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // The theme cookie is read on the server so the page arrives in the right
  // theme (lib/theme.ts). This makes pages render per request, which is fine
  // for an app that is behind sign-in.
  const choice = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <html lang="en" data-theme={themeAttribute(choice)}>
      <body>
        <ThemeProvider initial={choice}>{children}</ThemeProvider>
      </body>
    </html>
  );
}
