import type { Metadata, Viewport } from "next";
import { Archivo, Inter, JetBrains_Mono } from "next/font/google";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { ThemeProvider } from "@/components/ui/ThemeProvider";
import { parseTheme, THEME_COOKIE, themeAttribute } from "@/lib/theme";
import "./globals.css";

// MonoEdge's faces (monoedge.in), served from this app at build time, so no
// page asks Google for anything. app/globals.css maps them to --font-sans,
// --font-display and --font-mono, with Devanagari faces after them.
const inter = Inter({ subsets: ["latin", "latin-ext"], display: "swap", variable: "--font-inter" });
const archivo = Archivo({ subsets: ["latin", "latin-ext"], display: "swap", variable: "--font-archivo" });
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
    { media: "(prefers-color-scheme: light)", color: "#f6f5f2" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0a0f" },
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
      className={`${inter.variable} ${archivo.variable} ${jetbrainsMono.variable}`}
    >
      <body>
        <ThemeProvider initial={choice}>{children}</ThemeProvider>
      </body>
    </html>
  );
}
