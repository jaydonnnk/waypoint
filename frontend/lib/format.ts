import type { Offer } from "./types";

// Demo IATA -> country/city display names. Slice 2+ will surface the real
// country from the backend's iata map; the tracer keeps a small local map.
export const COUNTRY_NAMES: Record<string, string> = {
  SG: "Singapore",
  VN: "Vietnam",
  TH: "Thailand",
  KR: "South Korea",
  JP: "Japan",
  IN: "India",
};

export const CITY_NAMES: Record<string, string> = {
  SGN: "Ho Chi Minh City",
  DMK: "Bangkok",
  ICN: "Seoul",
  NRT: "Tokyo",
  SIN: "Singapore",
};

/** "960" minutes -> "16h". */
export function formatHours(totalMinutes: number): string {
  return `${Math.round(totalMinutes / 60)}h`;
}

/** The connecting airport for a (single-connection) offer. */
export function viaAirport(offer: Offer): string {
  return offer.segments.length > 1 ? offer.segments[0].arr_airport : "nonstop";
}
