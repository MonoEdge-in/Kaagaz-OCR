import type { Metadata, Viewport } from "next";
import { Archivo, Inter, JetBrains_Mono } from "next/font/google";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { ThemeProvider } from "@/components/ui/ThemeProvider";
import { WheelGuard } from "@/components/ui/WheelGuard";
import { parseTheme, THEME_COOKIE, themeAttribute } from "@/lib/theme";
import "./globals.css";

// The three faces, served from this app at build time, so no page asks Google
// for anything; next/font adds a size-adjusted fallback to each, so the swap
// moves nothing (UI audit G3/G4, 16 Oct 2026). Inter is the text and UI face,
// JetBrains Mono is for data only (values, IDs, numbers and scanned text: the
// .mono class), Archivo is the display face for headings. app/globals.css puts
// each first in --font-sans, --font-mono and --font-display, with Devanagari
// faces after them.
const inter = Inter({ subsets: ["latin", "latin-ext"], display: "swap", variable: "--font-inter" });
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin", "latin-ext"],
  display: "swap",
  variable: "--font-jetbrains-mono",
});
const archivo = Archivo({ subsets: ["latin", "latin-ext"], display: "swap", variable: "--font-archivo" });

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
      className={`${inter.variable} ${jetbrainsMono.variable} ${archivo.variable}`}
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
