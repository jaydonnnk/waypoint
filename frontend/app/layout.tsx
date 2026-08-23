import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Waypoint — the travel treasury desk",
  description:
    "A mandate-driven desk that marks travel positions to market, judges hold-vs-book, admits losses honestly, and escalates spikes to one human click.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
      {/* Runtime font loading (no next/font dependency). React 19 hoists
          these <link> tags into <head> — no manual <head> wrapper needed.
          Fallback fonts in globals.css cover offline rendering. */}
      <link rel="preconnect" href="https://fonts.googleapis.com" />
      <link
        rel="preconnect"
        href="https://fonts.gstatic.com"
        crossOrigin="anonymous"
      />
      <link
        href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,600;0,9..144,700;1,9..144,500&family=IBM+Plex+Mono:wght@400;600&display=swap"
        rel="stylesheet"
      />
    </html>
  );
}
