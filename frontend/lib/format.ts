// Generic display helpers. All money arrives as Decimal strings (may be
// negative); timestamps arrive as Python str(datetime) — "YYYY-MM-DD HH:MM:SS.ffffff"
// (space separator), so parsing is defensive throughout.

/** Currency-code -> display symbol, falling back to the raw code. */
const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: "$",
  EUR: "€",
  GBP: "£",
  SGD: "S$",
  JPY: "¥",
};

/** "1840.00" -> "$1,840.00" · "-62.5" -> "−$62.50". Unknown/absent
 * currency renders without a symbol; non-numeric input is returned
 * verbatim — the screens never fabricate a number. */
export function money(value: string, currency?: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  const symbol = currency
    ? CURRENCY_SYMBOLS[currency.toUpperCase()] ?? `${currency} `
    : "";
  const abs = Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return n < 0 ? `−${symbol}${abs}` : `${symbol}${abs}`;
}

/** P&L variant: keeps the sign explicit ("+$1,840.00" / "−$62.50"). */
export function signedMoney(value: string, currency?: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  if (n > 0) return `+${money(value, currency)}`;
  return money(value, currency);
}

/** Parse a Python str(datetime) / ISO timestamp defensively; null on junk. */
function parseStamp(value: string): Date | null {
  if (!value) return null;
  // "2026-08-23 14:05:01.123456" -> "2026-08-23T14:05:01.123456".
  const iso = value.trim().replace(" ", "T");
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "2026-08-23 14:05:01.123456" -> "Aug 23 · 14:05:01". Raw string as fallback. */
export function formatStamp(value: string): string {
  const d = parseStamp(value);
  if (!d) return value;
  const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString("en-US", { hour12: false });
  return `${date} · ${time}`;
}

/** Date-only slice of a timestamp ("2026-08-24" or a full stamp). */
export function formatDay(value: string): string {
  const d = parseStamp(value);
  if (!d) return value;
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
