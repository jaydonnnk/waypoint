import type { Metadata } from "next";
import "./globals.css";
import "./presentation.css";

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
    <html lang="en">
      <head>
        {/* Runtime font loading (no next/font dependency). The links live
            inside <head> (Next hoists this block) so React never renders a
            <link> as a child of <html>; `precedence` declares stylesheet
            order so React 19 doesn't warn about unknown precedence.
            Fallback fonts in globals.css cover offline rendering. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@500;600&display=swap"
          precedence="default"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
