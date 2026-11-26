const dec = (digits: number) =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const f2 = dec(2);
const f1 = dec(1);
const f3 = dec(3);
const f4 = dec(4);
const int = new Intl.NumberFormat("en-US");

export const fmtInt = (n: number) => int.format(n);
export const fmtBm25 = (n: number | null | undefined) => (n == null ? "—" : f2.format(n));
export const fmtSemantic = (n: number | null | undefined) => (n == null ? "—" : f3.format(n));
export const fmtRrf = (n: number | null | undefined) => (n == null ? "—" : f4.format(n));
export const fmtMetric = (n: number | null | undefined) => (n == null ? "—" : f3.format(n));
export const fmtMs = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 100 ? int.format(Math.round(n)) : n >= 1 ? f1.format(n) : f2.format(n)}\u00a0ms`;
export const pad2 = (n: number) => String(n).padStart(2, "0");
export const lineSpan = (start: number, end: number) => (end > start ? `Lines ${start}–${end}` : `Line ${start}`);

/** What each ranking mode matches on, in the searcher's words. */
export const MODE_LABEL = {
  hybrid: "Words + meaning",
  semantic: "Meaning",
  lexical: "Words",
} as const;

/** The engine underneath each mode: shown second, for the people who want it. */
export const MODE_ENGINE = {
  hybrid: "Hybrid RRF",
  semantic: "Cosine",
  lexical: "BM25",
} as const;
