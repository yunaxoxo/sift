// Presentation helpers. Nothing here scores or ranks.

const STOP = new Set(
  "a an the and or of to in on at for with is are was were be been it its this that these those as by from how what when why which who do does did not no vs can into than then so if".split(" "),
);

export function queryTerms(query: string): string[] {
  // Hyphenated words stay whole ("B-tree", "0-RTT"): split, "b" would be dropped and "tree" would stand alone.
  const words = query.toLowerCase().match(/[\p{L}\p{N}_]+(?:-[\p{L}\p{N}_]+)*/gu) ?? [];
  return [...new Set(words.filter((w) => w.length > 1 && !STOP.has(w)))];
}

/** A heading path such as `A > B > C` becomes a clean title (`C`) and the sections above it. */
export function sectionOf(context: string): { title: string; parents: string[] } {
  const parts = context
    .split(">")
    .map((p) => p.replace(/^[#\s]+/, "").trim())
    .filter(Boolean);
  return { title: parts.at(-1) ?? "", parents: parts.slice(0, -1) };
}

/** Whole-word, case-insensitive pattern for any of `terms`, longest first. */
export function termPattern(terms: string[]): RegExp {
  const escaped = [...terms].sort((a, b) => b.length - a.length).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`(?<![\\p{L}\\p{N}_])(${escaped.join("|")})(?![\\p{L}\\p{N}_])`, "giu");
}

/** Which of the query's terms appear in `text`, in query order. The same rule the highlights use. */
export function termsIn(text: string, terms: string[]): string[] {
  if (!terms.length) return [];
  const seen = new Set([...text.matchAll(termPattern(terms))].map((m) => m[0].toLowerCase()));
  return terms.filter((t) => seen.has(t));
}

const escapeRe = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * The longest run of the query's words that appear next to each other, in query order, in `text`
 * ("page tables" in "page tables are…"). Null when no two of them sit together.
 */
export function phraseIn(text: string, terms: string[]): string[] | null {
  let best: string[] = [];
  for (let i = 0; i < terms.length - 1; i++) {
    let run = [terms[i]!];
    for (let j = i + 1; j < terms.length; j++) {
      const next = [...run, terms[j]!];
      const re = new RegExp(`(?<![\\p{L}\\p{N}_])${next.map(escapeRe).join("\\s+")}(?![\\p{L}\\p{N}_])`, "iu");
      if (!re.test(text)) break;
      run = next;
    }
    if (run.length > best.length) best = run;
  }
  return best.length >= 2 ? best : null;
}

/** The class a `<mark>` takes: flush against a hyphen on either side, so "page-table" reads as one word. */
export const markClass = (part: { tight?: "start" | "end" | "both" }) => (part.tight ? `tight-${part.tight}` : undefined);

/** Split `text` into alternating plain and matched runs, for `<mark>`. */
export function splitMatches(text: string, terms: string[]): { text: string; hit: boolean; tight?: "start" | "end" | "both" }[] {
  if (!terms.length) return [{ text, hit: false }];
  const re = termPattern(terms);
  const parts: { text: string; hit: boolean; tight?: "start" | "end" | "both" }[] = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    const gap = text.slice(last, m.index);
    const prev = parts.at(-1);
    // Matched words separated only by space read as one phrase: one chip, not two with a seam.
    if (prev?.hit && gap && !gap.trim()) prev.text += gap + m[0];
    else {
      if (gap) parts.push({ text: gap, hit: false });
      parts.push({ text: m[0], hit: true });
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), hit: false });
  parts.forEach((part, i) => {
    if (!part.hit) return;
    const start = parts[i - 1]?.text.endsWith("-");
    const end = parts[i + 1]?.text.startsWith("-");
    if (start || end) part.tight = start && end ? "both" : start ? "start" : "end";
  });
  return parts;
}

/**
 * The plain prose of a chunk: code, tables, headings and inline markup left out. This is the text the
 * result list can show, so it is also the text a claim about a result must be checked against.
 */
export function proseOf(markdown: string): string {
  const prose: string[] = [];
  let fenced = false;
  // Indented code (4 spaces or a tab) only counts after a blank line, so indented list continuations stay prose.
  let indented = false;
  let blank = true;
  for (const line of markdown.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) {
      fenced = !fenced;
      continue;
    }
    const isBlank = !line.trim();
    if (!isBlank) indented = /^( {4}|\t)/.test(line) && (blank || indented);
    blank = isBlank;
    if (fenced || indented || isBlank || /^\s*(\||#{1,6}\s|[-*_]{3,}\s*$)/.test(line)) continue;
    prose.push(
      line
        .replace(/^\s*(>\s*)+/, "")
        .replace(/^\s*([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?/, "")
        .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/(\*\*|__|\*|`)/g, ""), // single "_" stays: snake_case words are common in notes
    );
  }
  return prose.join(" ").replace(/\s+/g, " ").trim();
}

/**
 * A short plain-prose window of a chunk for the result list: the text around the first of the
 * searcher's words. Returns null when the chunk has no prose at all.
 */
export function snippet(markdown: string, terms: string[], size = 240): string | null {
  const text = proseOf(markdown);
  if (!text) return null;
  const first = terms.length ? termPattern(terms).exec(text)?.index ?? 0 : 0;
  // Start a little before the first match, at a word boundary, so the match sits in the first line.
  let start = Math.max(0, first - Math.round(size / 3));
  if (start > 0) start = text.indexOf(" ", start) + 1 || start;
  let end = Math.min(text.length, start + size);
  if (end < text.length) end = text.lastIndexOf(" ", end) > start ? text.lastIndexOf(" ", end) : end;
  return `${start > 0 ? "… " : ""}${text.slice(start, end).trim()}${end < text.length ? " …" : ""}`;
}
