import type { Offer } from "./types";

// ISO-2 -> DISPLAY names only. Slice 2+: the airport -> country mapping
// itself lives in the backend (data/iata_country.csv) and rides on the
// wire as Layover.country; this map just humanizes those codes.
export const COUNTRY_NAMES: Record<string, string> = {
  SG: "Singapore",
  JP: "Japan",
  VN: "Vietnam",
  TH: "Thailand",
  KR: "South Korea",
  IN: "India",
  TW: "Taiwan",
  HK: "Hong Kong",
  PH: "Philippines",
  MY: "Malaysia",
  ID: "Indonesia",
  LK: "Sri Lanka",
  NP: "Nepal",
  BD: "Bangladesh",
  MV: "Maldives",
  MM: "Myanmar",
  BN: "Brunei",
  KH: "Cambodia",
  CN: "China",
  US: "United States",
  GU: "Guam",
  AU: "Australia",
  NZ: "New Zealand",
  AE: "UAE",
  QA: "Qatar",
  TR: "Türkiye",
  NL: "Netherlands",
  DE: "Germany",
  GB: "United Kingdom",
  FR: "France",
};

/** "960" minutes -> "16h". */
export function formatHours(totalMinutes: number): string {
  return `${Math.round(totalMinutes / 60)}h`;
}

/** The connecting airport for a (single-connection) offer. */
export function viaAirport(offer: Offer): string {
  return offer.segments.length > 1 ? offer.segments[0].arr_airport : "nonstop";
}
