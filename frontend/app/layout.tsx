import type { Metadata, Viewport } from "next";
import { JetBrains_Mono } from "next/font/google";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { ThemeProvider } from "@/components/ui/ThemeProvider";
import { WheelGuard } from "@/components/ui/WheelGuard";
import { parseTheme, THEME_COOKIE, themeAttribute } from "@/lib/theme";
import "./globals.css";

// JetBrains Mono, the MonoEdge text and UI face, served from this app at build
// time, so no page asks Google for anything; next/font adds a size-adjusted
// fallback, so the swap moves nothing. app/globals.css puts it first in
// --font-mono, with Devanagari faces after it. Headings use --font-display, a
// fallback stack (Monument Extended has no web licence here).
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin", "latin-ext"],
  display: "swap",
  variable: "--font-jetbrains-mono",
});

// Tab titles stay generic: browser history syncs off the device, so no
// document or customer name ever goes here. A section sets only its own
// fixed name ("Documents · Kaagaz · MonoEdge").
export const metadata: Metadata = {
  title: { default: "Kaagaz · MonoEdge", template: "%s · Kaagaz · MonoEdge" },
  description: "Ask your business documents. Every answer shows its source.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  colorScheme: "light dark",
  themeColor: [
    // The header's colour (--bg-surface), on the app and the sign-in pages.
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#101114" },
  ],
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // The theme cookie is read on the server so the page arrives in the right
  // theme (lib/theme.ts). This makes pages render per request, which is fine
  // for an app that is behind sign-in.
  const choice = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <html
      lang="en"
      data-theme={themeAttribute(choice)}
      className={jetbrainsMono.variable}
      // Next turns smooth scrolling off while it moves between pages.
      data-scroll-behavior="smooth"
    >
      <body>
        <WheelGuard />
        <ThemeProvider initial={choice}>{children}</ThemeProvider>
      </body>
    </html>
  );
}
