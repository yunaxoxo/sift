import { useLayoutEffect, useState, type RefObject } from "react";
import { fmtMs, MODE_ENGINE, MODE_LABEL } from "../lib/format";
import { rovingKeys } from "../lib/roving";
import type { IndexRun, Mode, TopK } from "../types";

const MODES: Mode[] = ["hybrid", "semantic", "lexical"];
const KS: TopK[] = [3, 5, 10];

/** The file's own name; its folder too when two files in the list share a name. */
function label(path: string, all: [string, number][]): string {
  const parts = path.split("/");
  const name = parts.at(-1) ?? path;
  const clash = all.filter(([p]) => p.split("/").at(-1) === name).length > 1;
  return clash && parts.length > 1 ? parts.slice(-2).join("/") : name;
}

/** A latency with its decimal point in a fixed column: 3.7, 0.96 and 123 line up down the list. */
function Ms({ value }: { value: number | null | undefined }) {
  const [num = "", unit] = fmtMs(value).split(" ");
  const [whole, frac] = num.split(".");
  return (
    <>
      {whole}
      <span className="f">{frac ? `.${frac}` : ""}</span>
      {unit && ` ${unit}`}
    </>
  );
}

interface Props {
  text: string;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onText: (text: string) => void;
  onSubmit: () => void;
  /** The committed search, to tell a new query from pressing Enter on the current one. */
  query: string;
  examples: string[];
  onExample: (query: string) => void;
  mode: Mode;
  onMode: (mode: Mode) => void;
  k: TopK;
  onK: (k: TopK) => void;
  /** Files among the retrieved candidates, with how many chunks of each matched. */
  sources: [string, number][];
  scope: string | null;
  onScope: (path: string | null) => void;
  onClear: () => void;
  timings: Record<string, number> | null;
  indexRun: IndexRun | null;
  /** Answering from the bundled sample rather than a live index: only recorded searches return results. */
  fixture: boolean;
  /** The current search has results to show. */
  hasResults: boolean;
  /** The samples are offered in the results column (nothing searched yet, or a miss), so the sidebar leaves them out. */
  miss: boolean;
}

export function Controls({ text, inputRef, onText, onSubmit, query, examples, onExample, mode, onMode, k, onK, sources, scope, onScope, onClear, timings, indexRun, fixture, hasResults, miss }: Props) {
  // Per-query stages only. A stage that did not run in this mode shows "—", not a measured zero.
  const stages: { label: string; value: number | null | undefined }[] = [
    { label: "Whole search", value: timings?.total_ms },
    { label: "Embed", value: mode === "lexical" ? null : timings?.embed_ms },
    { label: "BM25", value: mode === "semantic" ? null : timings?.lexical_ms },
    { label: "Cosine", value: mode === "lexical" ? null : timings?.vector_ms },
    { label: "RRF Fusion", value: mode === "hybrid" ? timings?.fusion_ms : null },
  ];

  const [samplesOpen, setSamplesOpen] = useState(false);
  const sampleList = (
    <ul className="samples" aria-label="Sample queries">
      {examples.slice(0, 3).map((q) => (
        <li key={q}>
          <button
            type="button"
            onClick={() => {
              setSamplesOpen(false); // fold away before the field re-wraps, so nothing jumps under the pointer
              onExample(q);
            }}
          >
            {q}
          </button>
        </li>
      ))}
    </ul>
  );
  // On sample data these are the only searches that answer. With nothing to show they lead; once a
  // search has results they fold to one line so the results come first, most of all on a phone.
  const samples =
    examples.length === 0 || miss ? null : hasResults ? (
      <details className="samples-fold" open={samplesOpen} onToggle={(e) => setSamplesOpen(e.currentTarget.open)}>
        <summary>Try a sample search</summary>
        {sampleList}
      </details>
    ) : (
      <>
        {fixture && <p className="hint sample-note">Sample data: only these searches have recorded results.</p>}
        {sampleList}
      </>
    );

  const [open, setOpen] = useState(false);
  const [allSources, setAllSources] = useState(false);

  // Grow the field to fit its text: wrapped lines, never a scrollbar for a normal query. Re-fit whenever
  // the field's width changes (window resize, or the page scrollbar appearing once results arrive) and
  // when its web font arrives, whose glyphs are wider than the fallback's.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    const fit = () => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    };
    fit();
    document.fonts?.load(getComputedStyle(el).font).then(fit, () => {});
    let width = el.clientWidth;
    const watch = new ResizeObserver(() => {
      if (el.clientWidth === width) return; // our own height change: nothing to do
      width = el.clientWidth;
      fit();
    });
    watch.observe(el);
    return () => watch.disconnect();
  }, [text, inputRef]);

  // Source helps only when the passages come from more than one file. A long list shows the top five;
  // the chosen file always stays visible.
  const SOURCE_CAP = 5;
  // Once there are results Source is always there, so the sidebar keeps its shape between modes;
  // with a single file it is a plain line, not a choice.
  const showSources = sources.length > 0 || scope !== null;
  // One file means nothing to choose between.
  const soleSource = sources.length === 1 && scope === null;
  const visibleSources = allSources ? sources : sources.filter(([path], i) => i < SOURCE_CAP || path === scope);
  const hiddenSources = sources.length - visibleSources.length;
  const ran = stages.some(({ value }) => value != null);

  return (
    <aside className="controls" aria-label="Search controls">
      <div className="controls-inner">
      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
      >
        <label className="visually-hidden" htmlFor="query">
          Search your notes
        </label>
        <div className="query">
          {/* A one-line field that wraps instead of clipping, so a long query stays readable. Enter
              searches (never a newline); pasted line breaks become spaces. */}
          <textarea
            id="query"
            ref={inputRef}
            rows={1}
            value={text}
            onChange={(e) => onText(e.target.value.replace(/\s*\n\s*/g, " "))}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault(); // never a newline
                // A changed query is a new search: keep this Enter from also opening a result of the old one.
                // An unchanged query lets Enter through to open the active result.
                if (e.currentTarget.value.trim() !== query) {
                  e.stopPropagation();
                  onSubmit();
                }
              }
            }}
            placeholder="Search your notes…"
            name="q"
            autoComplete="off"
            spellCheck={false}
            enterKeyHint="search"
            aria-multiline="false"
            maxLength={500}
          />
          {text ? (
            <button type="button" className="clear" onClick={onClear} aria-label="Clear search">
              ×
            </button>
          ) : (
            <kbd aria-hidden="true">/</kbd>
          )}
        </div>
      </form>

      {fixture && !hasResults && samples}

      <button type="button" className="disclosure" aria-expanded={open} aria-controls="filters-panel" onClick={() => setOpen((v) => !v)}>
        <span>Options</span>
        <span className="muted">
          {MODE_LABEL[mode]} <span aria-hidden="true">·</span> {open ? "Hide" : "Show"}
        </span>
      </button>

      <div id="filters-panel" className="panel" data-open={open}>
      {(!fixture || hasResults) && samples}

      <section className="group" aria-labelledby="g-mode">
        <h2 id="g-mode">Ranking</h2>
        <div role="radiogroup" aria-labelledby="g-mode" className="options options-stack" onKeyDown={rovingKeys("[role=radio]")}>
          {MODES.map((m) => (
            <button key={m} type="button" role="radio" aria-checked={mode === m} tabIndex={mode === m ? 0 : -1} onClick={() => onMode(m)}>
              <span>{MODE_LABEL[m]}</span>
              <span className="engine">{MODE_ENGINE[m]}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="group" aria-labelledby="g-k">
        <h2 id="g-k">Show</h2>
        <div role="radiogroup" aria-labelledby="g-k" className="options options-row" onKeyDown={rovingKeys("[role=radio]")}>
          {KS.map((n) => (
            <button key={n} type="button" role="radio" aria-checked={k === n} tabIndex={k === n ? 0 : -1} onClick={() => onK(n)}>
              <span className="num">{n}</span>
            </button>
          ))}
        </div>
      </section>

      {showSources && (
        <section className="group" aria-labelledby="g-source">
          <h2 id="g-source">Source</h2>
          {soleSource ? (
            // One file means nothing to choose between: a plain label; the list says "All from …".
            <p className="single-source">
              <span translate="no">{label(sources[0]![0], sources)}</span>
              <span className="count num">{sources[0]![1]}</span>
            </p>
          ) : (
          <div role="radiogroup" aria-labelledby="g-source" className="options options-stack sources" onKeyDown={rovingKeys("[role=radio]")}>
            <button type="button" role="radio" aria-checked={scope === null} tabIndex={scope === null ? 0 : -1} aria-label={`All files, ${sources.reduce((n, [, c]) => n + c, 0)} passages`} onClick={() => onScope(null)}>
              <span>All files</span>
              <span className="count num">{sources.reduce((n, [, c]) => n + c, 0)}</span>
            </button>
            {visibleSources.map(([path, count]) => (
              <button key={path} type="button" role="radio" aria-checked={scope === path} tabIndex={scope === path ? 0 : -1} title={path} aria-label={`${label(path, sources)}, ${count} ${count === 1 ? "passage" : "passages"}`} onClick={() => onScope(path)}>
                <span translate="no">{label(path, sources)}</span>
                <span className="count num">{count}</span>
              </button>
            ))}
          </div>
          )}
          {!soleSource && (hiddenSources > 0 || allSources) && sources.length > SOURCE_CAP && (
            <button type="button" className="link more" onClick={() => setAllSources((v) => !v)} aria-expanded={allSources}>
              {allSources ? "Show fewer" : `${hiddenSources} more`}
            </button>
          )}
          <p className="hint">Counts are the passages found for this search.</p>
        </section>
      )}

      {/* Engine detail, folded by default: the result list leads, and its tally line carries the total. */}
      {ran && (
      <details className="group timing">
        <summary>
          <span className="group-title">Timing</span>
        </summary>
        <dl className="latency">
          {stages.map(({ label, value }) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd className="num">
                <Ms value={value} />
              </dd>
            </div>
          ))}
        </dl>
        {indexRun?.chunk_ms != null && (
          <p className="hint">
            Indexing walked and chunked the files in <Ms value={indexRun.chunk_ms} />.
          </p>
        )}
      </details>
      )}
      </div>
      </div>
    </aside>
  );
}
