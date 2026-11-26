import { memo, useEffect, useMemo, useRef, type RefObject } from "react";
import { fmtBm25, fmtInt, fmtRrf, fmtSemantic, lineSpan, MODE_LABEL, pad2 } from "../lib/format";
import { markClass, phraseIn, proseOf, sectionOf, snippet, splitMatches, termsIn } from "../lib/text";
import type { Hit, Mode, Provenance, SearchResult } from "../types";

interface Props {
  result: SearchResult | null;
  query: string;
  loading: boolean;
  error: string | null;
  terms: string[];
  activeIndex: number;
  onActive: (index: number) => void;
  onOpen: (hit: Hit, trigger: HTMLElement) => void;
  onRetry: () => void;
  onExample: (query: string) => void;
  examples: string[];
  scope: string | null;
  onClearScope: () => void;
  listRef: RefObject<HTMLOListElement | null>;
  /** Said when a mode switch dropped the Source filter because that file has nothing in the new mode. */
  scopeNote: string | null;
  /** Answering from the bundled sample data, where only a few searches have recorded results. */
  sample: boolean;
  /** How many retrieved chunks pass the Source filter, before Show cuts the list. */
  total: number;
}


/** A term as the searcher typed it ("LSM", not "lsm"). */
const asTyped = (term: string, query: string) => query.match(new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"))?.[0] ?? term;

/**
 * The searcher's evidence for a result, said so that it tells neighbours apart: which of their words the
 * passage contains (and which sit only in its heading), then the most distinctive thing about it: a word
 * no other shown result has, or a phrase that matches together. How it was found is added only when
 * that is unusual. Words it lacks are not listed: the check is whole-word, and the word ranker also
 * matches other forms of a word.
 */
export function evidence(hit: Hit, terms: string[], mode: Mode, query: string, siblings: Hit[] = []): string | null {
  // Claims are checked against what the row can show: the prose its excerpt is cut from, and its headings.
  const prose = proseOf(hit.text);
  const { title, parents } = sectionOf(hit.heading_context);
  const headings = [...parents, title].filter(Boolean);
  const visible = `${headings.join("\n")}\n${prose}`;
  const typed = (ts: string[]) => ts.map((t) => asTyped(t, query));
  const inProse = termsIn(prose, terms);
  const has = termsIn(visible, terms);
  // Words the passage has but cannot show here: only in a code block or table.
  const hidden = terms.length && !has.length ? termsIn(hit.text, terms) : [];

  // Where words that are in a heading but not the prose sit: the row's own title, or a section above it.
  const headOnly = has.filter((t) => !inProse.includes(t));
  const where = new Map<string, string[]>();
  for (const t of headOnly) {
    const home = headings.find((h) => termsIn(h, [t]).length > 0);
    const place = home === undefined || home === title ? "the title" : `the section “${home}”`;
    where.set(place, [...(where.get(place) ?? []), t]);
  }
  const placed = [...where].map(([place, ts]) => `${typed(ts).join(", ")} in ${place}`).join("; ");

  let words: string | null = null;
  if (terms.length) {
    if (has.length && headOnly.length === has.length) words = placed;
    else if (has.length === terms.length && terms.length > 1) words = `Contains all your words${placed ? ` (${placed})` : ""}`;
    else if (has.length) words = `Contains ${typed(has).join(", ")}${placed ? ` (${placed})` : ""}`;
    else if (hidden.length) words = `${typed(hidden).join(", ")} only in a code block or table`;
    else if (hit.lexical_rank != null) words = "Contains a form of your words";
    else words = "None of your words";
  }

  // What this passage has that the others shown do not.
  const only =
    siblings.length > 1
      ? has.filter((t) => siblings.filter((h) => termsIn(`${h.heading_context}\n${proseOf(h.text)}`, [t]).length > 0).length === 1).slice(0, 2)
      : [];
  // A phrase only counts as distinctive when at most half the shown results share it.
  let phrase = phraseIn(visible, terms);
  if (phrase && siblings.length > 1) {
    const key = phrase.join(" ");
    const sharing = siblings.filter((h) => phraseIn(`${h.heading_context}\n${proseOf(h.text)}`, terms)?.join(" ") === key).length;
    if (sharing > siblings.length / 2) phrase = null;
  }
  const distinct = only.length ? `only result with ${typed(only).join(" and ")}` : phrase ? `“${typed(phrase).join(" ")}” together` : null;

  const byWords = hit.lexical_rank != null, byMeaning = hit.semantic_rank != null;
  const origin =
    mode === "hybrid"
      ? byWords && !byMeaning
        ? "found by words only"
        : byMeaning && !byWords
          ? "found by meaning only"
          : null
      : mode === "semantic" && !has.length
        ? "found by meaning"
        : null;
  return [words, distinct, origin].filter(Boolean).join(" · ") || null;
}

/** Where each ranker put the hit, in the searcher's words: "#3 by words · #1 by meaning". */
export const byRanker = (hit: Hit) =>
  [hit.lexical_rank != null && `#${hit.lexical_rank} by words`, hit.semantic_rank != null && `#${hit.semantic_rank} by meaning`].filter(Boolean).join(" · ");

/** §13 detail for the reader's Scores: how far fusion moved the hit from each list, and each word's BM25 share. */
function explained(p: Provenance, mode: Mode): string[] {
  const shift = (n: number | null, list: string) =>
    n == null ? null : n === 0 ? `level with its ${list} rank` : `${Math.abs(n)} ${n > 0 ? "up" : "down"} from its ${list} rank`;
  const moved = [shift(p.lift.lexical, "words"), shift(p.lift.semantic, "meaning")].filter(Boolean).join(" · ");
  // tf counts the passage's analysed text only, so a word that sits in a heading can still read 0 here.
  const terms = p.lexical.terms.map((t) => (t.tf ? `${t.term} ${fmtBm25(t.contribution)}` : `${t.term} 0 (not in the passage text)`)).join(" · ");
  return [mode === "hybrid" && moved && `Fusion: ${moved}`, terms && `BM25 by word: ${terms}`].filter((l): l is string => !!l);
}

/** The engine's own numbers: kept for the reader, out of the result list. */
export function Metrics({ hit, mode, was }: { hit: Hit; mode: Mode; was?: number | "new" | null }) {
  const items: [string, string][] =
    mode === "lexical"
      ? [["BM25", fmtBm25(hit.lexical_score ?? hit.score)]]
      : mode === "semantic"
        ? [["Cosine", fmtSemantic(hit.semantic_score ?? hit.score)]]
        : [
            ["BM25", fmtBm25(hit.lexical_score)],
            ["Cosine", fmtSemantic(hit.semantic_score)],
            ["RRF", fmtRrf(hit.score)],
          ];
  // Fusion works on positions, not scores, so the positions come first.
  const ranks = [
    byRanker(hit),
    // Words and Meaning are compared with Words + meaning here, not in the list.
    mode !== "hybrid" && was != null && (was === "new" ? "outside the Words + meaning top 50" : `was #${was} in Words + meaning`),
  ].filter(Boolean);
  return (
    <>
      {ranks.length > 0 && <p className="ranks num">{ranks.join(" · ")}</p>}
      {hit.provenance && explained(hit.provenance, mode).map((line) => (
        <p key={line} className="ranks num">
          {line}
        </p>
      ))}
      <p className="metrics num">
        {items.map(([label, value]) => (
          <span key={label}>
            {label}: {value}
          </span>
        ))}
      </p>
    </>
  );
}

/** "Try a, b, c.": the sample searches that have recorded results, as links. */
function Samples({ examples, onExample }: { examples: string[]; onExample: (query: string) => void }) {
  return (
    <>
      Try{" "}
      {examples.slice(0, 3).map((q, i) => (
        <span key={q}>
          {i > 0 && ", "}
          <button type="button" className="link" onClick={() => onExample(q)}>
            {q}
          </button>
        </span>
      ))}
      .
    </>
  );
}

/** A few plain lines around the first of the searcher's words; tables and code are left to the reader. */
const Excerpt = memo(function Excerpt({ hit, terms }: { hit: Hit; terms: string[] }) {
  const text = useMemo(() => snippet(hit.text, terms), [hit.text, terms]);
  if (text === null) {
    // A heading-only passage says nothing more than its title; a code or table passage gets a pointer.
    if (hit.text.split("\n").every((l) => !l.trim() || /^\s*#/.test(l))) return null;
    return <p className="excerpt muted">Code or a table: open the note to read it.</p>;
  }
  return (
    <p className="excerpt">
      {splitMatches(text, terms).map((part, i) => (part.hit ? <mark key={i} className={markClass(part)}>{part.text}</mark> : part.text))}
    </p>
  );
});

export function Results({ result, query, loading, error, terms, activeIndex, onActive, onOpen, onRetry, onExample, examples, scope, onClearScope, listRef, scopeNote, sample, total }: Props) {
  const activeRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, result]);

  if (error) {
    return (
      <div className="stream" role="alert">
        <p className="note">
          Couldn’t run that search. <span className="muted">{error}</span>
        </p>
        <button type="button" className="link" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }

  if (!query) {
    return (
      <div className="stream">
        <p className="note">Type a question or a few keywords. sift ranks the passages of your notes that answer it.</p>
        {sample && (
          <p className="note muted">
            Sample data: only a few searches have recorded results. <Samples examples={examples} onExample={onExample} />
          </p>
        )}
      </div>
    );
  }

  if (!result) {
    return (
      <div className="stream" aria-busy="true">
        <p className="note muted">Searching…</p>
      </div>
    );
  }

  if (result.preview_miss) {
    return (
      <div className="stream">
        <p className="note">The sample data has no recorded results for “{result.query}”.</p>
        {examples.length > 0 && (
          <p className="note muted">
            <Samples examples={examples} onExample={onExample} />
          </p>
        )}
        <p className="note muted">
          To search your own notes, run <code>sift index &lt;folder&gt;</code>, then <code>sift ui</code> again.
        </p>
      </div>
    );
  }

  if (result.hits.length === 0) {
    return (
      <div className="stream">
        {scope ? (
          <>
            <p className="note">
              None of the passages for “{result.query}” come from <span className="path num">{scope.split("/").at(-1)}</span>.
            </p>
            <button type="button" className="link" onClick={onClearScope}>
              Show all files
            </button>
          </>
        ) : (
          <p className="note">Nothing matched “{result.query}” by {MODE_LABEL[result.mode].toLowerCase()}. Try fewer or different words.</p>
        )}
      </div>
    );
  }

  // When every row is the same note, its path is said once above the list, not on each row.
  const sharedPath = !scope && result.hits.length > 1 && result.hits.every((h) => h.file_path === result.hits[0]!.file_path) ? result.hits[0]!.file_path : null;

  return (
    <div className="stream" data-loading={loading || undefined}>
      <h2 className="visually-hidden">Results</h2>
      <p className="tally num">
        {result.hits.length === total
          ? `${fmtInt(total)} ${total === 1 ? "passage" : "passages"}`
          : `${result.hits.length} of ${fmtInt(total)} passages`}{" "}
        for <q className="echo">{result.query}</q>
      </p>
      {scopeNote && (
        <p className="compare">
          {scopeNote}
          {sharedPath && (
            <>
              {" "}
              All of them are from <span className="path num" translate="no">{sharedPath}</span>.
            </>
          )}
        </p>
      )}
      {scope && (
        <p className="compare">
          Showing only <span className="path num" translate="no">{scope}</span>{" "}
          <span aria-hidden="true">·</span>{" "}
          <button type="button" className="link" onClick={onClearScope}>
            Show all files
          </button>
        </p>
      )}
      {sharedPath && !scopeNote && (
        <p className="compare">
          All from <span className="path num" translate="no">{sharedPath}</span>
        </p>
      )}
      <ol ref={listRef} className="results" key={result.query}>
        {result.hits.map((hit, index) => {
          const { title } = sectionOf(hit.heading_context);
          const found = evidence(hit, terms, result.mode, result.query, result.hits);
          const active = index === activeIndex;
          return (
            <li
              key={hit.chunk_id}
              ref={active ? activeRef : undefined}
              className="result"
              aria-current={active || undefined}
              data-active={active || undefined}
              // A keyboard-focused title keeps the active row: the pointer passing over must not move it elsewhere.
              onMouseMove={() => !active && !document.activeElement?.matches(".title:focus-visible") && onActive(index)}
              // The row is a mouse convenience, not the control: the title button inside it is the
              // keyboard and screen-reader path to the same action, so nothing is lost without a
              // pointer. A stretched link would make excerpt text unselectable.
              onClick={(e) => {
                if (window.getSelection()?.toString()) return; // let people select text
                const trigger = (e.currentTarget.querySelector("button.title") as HTMLElement | null) ?? e.currentTarget;
                onOpen(hit, trigger);
              }}
            >
              <div className="rank num">
                <span className="rank-n">{pad2(hit.rank)}</span>
              </div>
              <div className="body">
                <div className="head">
                  <h3>
                    <button
                      type="button"
                      className="title"
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpen(hit, e.currentTarget);
                      }}
                      onFocus={() => !active && onActive(index)}
                      aria-describedby={`${found ? `ev-${hit.chunk_id} ` : ""}${hit.provenance && byRanker(hit) ? `by-${hit.chunk_id} ` : ""}where-${hit.chunk_id}`}
                    >
                      {title || hit.file_path.split("/").at(-1)}
                    </button>
                  </h3>
                  <span className="cue" aria-hidden="true">
                    Open note
                  </span>
                </div>
                {found && (
                  <p className="found" id={`ev-${hit.chunk_id}`}>
                    {found}
                  </p>
                )}
                <p className="where num" id={`where-${hit.chunk_id}`}>
                  {/* Paths break only after a slash, never inside a name; said once when every row shares it. */}
                  {!sharedPath && !scope && (
                    <span className="path" translate="no">
                      {hit.file_path.split("/").map((part, i, all) => (
                        <span key={i}>
                          {part}
                          {i < all.length - 1 && (
                            <>
                              /<wbr />
                            </>
                          )}
                        </span>
                      ))}
                    </span>
                  )}
                  <span>{lineSpan(hit.start_line, hit.end_line)}</span>
                </p>
                {hit.provenance && byRanker(hit) && (
                  <p className="where" id={`by-${hit.chunk_id}`}>
                    {byRanker(hit)}
                  </p>
                )}
                <Excerpt hit={hit} terms={terms} />
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
