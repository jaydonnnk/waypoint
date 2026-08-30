import type { Metadata } from "next";
import { Figtree, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import "./presentation.css";

/* Pass 11 (demo hardening). The two faces used to be fetched from
   fonts.googleapis.com at RUNTIME, from the viewer's browser. Behind a
   captive portal, on conference wifi, or on any machine that cannot reach
   Google, the entire typography layer — every size, weight and tracking
   decision in globals.css — silently rendered in a fallback face instead.

   next/font/google downloads both families AT BUILD TIME and serves them
   from this origin, so the page has no third-party font dependency at all
   and the exact weights the CSS asks for are guaranteed to be present.

   The mono list is 400/500/600/700, not the 500/600 the old <link> asked
   for. MEASURED in the running build by walking every element whose
   computed font-family resolves to IBM Plex Mono: the weights actually in
   use are 400 (the desk id, the fare-move route and its "was" figure),
   600 (IATA codes) and 700 (.tc-delta and .fm-now — the price move and the
   current fare). With only 500/600 loaded, the 700s were being
   SYNTHESISED, and a faux-bold monospace figure sits next to a real one in
   the same row of the same card. Two more real faces cost less than that
   looks. `display: swap` keeps first paint immediate; the CSS variables
   below are what globals.css's --sans / --mono resolve to. */
const figtree = Figtree({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
  variable: "--font-figtree",
  fallback: ["system-ui", "sans-serif"],
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-plex-mono",
  fallback: ["ui-monospace", "monospace"],
});

export const metadata: Metadata = {
  title: "Waypoint — book your team's flights, on budget",
  description:
    "Waypoint books your team's trips, keeps an eye on the fares, and asks you first whenever a call is too big to make on its own.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${figtree.variable} ${plexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
