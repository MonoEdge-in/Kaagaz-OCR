import type { Metadata, Viewport } from "next";
import { Archivo, IBM_Plex_Mono, IBM_Plex_Sans, IBM_Plex_Sans_Devanagari } from "next/font/google";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { PaletteProvider } from "@/components/ui/PaletteProvider";
import { ThemeProvider } from "@/components/ui/ThemeProvider";
import { WheelGuard } from "@/components/ui/WheelGuard";
import { PALETTE_COOKIE, paletteAttribute, parsePalette } from "@/lib/palette";
import { parseTheme, THEME_COOKIE, themeAttribute } from "@/lib/theme";
import "./globals.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/components.css";

// The handoff's four faces, served from this app at build time, so no page asks
// Google for anything; next/font adds a size-adjusted fallback to each, so the
// swap moves nothing. app/styles/tokens.css stacks them in --k-font-display,
// --k-font-body and --k-font-mono, which app/globals.css's --font-* point at.
// Archivo carries its width axis too: the handoff sets headings at wdth 112
// (--k-display-stretch). next/font only takes axes with the whole variable
// weight range, so the file covers wght 100 to 900 and wdth 62 to 125.
const archivo = Archivo({
  subsets: ["latin", "latin-ext"],
  display: "swap",
  axes: ["wdth"],
  variable: "--font-archivo",
});
// IBM Plex Sans for text and UI, Plex Sans Devanagari for Hindi and Marathi,
// Plex Mono for data and labels.
const plexSans = IBM_Plex_Sans({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-plex-sans",
});
// Not preloaded: most pages carry no Devanagari, and the face's unicode range
// means a browser fetches it only when a page does.
const plexDevanagari = IBM_Plex_Sans_Devanagari({
  subsets: ["devanagari", "latin"], // its own Latin too, so Safari never falls back to Kohinoor
  weight: ["400", "500", "600"],
  display: "swap",
  preload: false,
  variable: "--font-plex-devanagari",
});
const plexMono = IBM_Plex_Mono({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500"],
  display: "swap",
  variable: "--font-plex-mono",
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
    // The page's ground (--bg-page), on the app and the sign-in pages.
    { media: "(prefers-color-scheme: light)", color: "#f6f5f1" },
    { media: "(prefers-color-scheme: dark)", color: "#111113" },
  ],
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // The theme cookie is read on the server so the page arrives in the right
  // theme (lib/theme.ts). This makes pages render per request, which is fine
  // for an app that is behind sign-in. The colour palette the same way (lib/palette.ts).
  const jar = await cookies();
  const choice = parseTheme(jar.get(THEME_COOKIE)?.value);
  const palette = parsePalette(jar.get(PALETTE_COOKIE)?.value);
  return (
    <html
      lang="en"
      data-theme={themeAttribute(choice)}
      data-palette={paletteAttribute(palette)}
      className={`${archivo.variable} ${plexSans.variable} ${plexDevanagari.variable} ${plexMono.variable}`}
      // Next turns smooth scrolling off while it moves between pages.
      data-scroll-behavior="smooth"
    >
      <body>
        <WheelGuard />
        <ThemeProvider initial={choice}>
          <PaletteProvider initial={palette}>{children}</PaletteProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
