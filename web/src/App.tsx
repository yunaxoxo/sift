import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { Benchmarks } from "./components/Benchmarks";
import { Controls } from "./components/Controls";
import { Keymap } from "./components/Keymap";
import { Reader } from "./components/Reader";
import { evidence, Results } from "./components/Results";
import { Topbar } from "./components/Topbar";
import { MODE_LABEL } from "./lib/format";
import { rankMap } from "./lib/rank";
import { queryTerms, sectionOf } from "./lib/text";
import type { Benchmark, Hit, Mode, SearchResult, Status, TopK, View } from "./types";

const MODES: Mode[] = ["hybrid", "semantic", "lexical"];
const KS: TopK[] = [3, 5, 10];
// Retrieval always asks for a pool this large; Show and the Source filter then work on it instantly.
const POOL = 50;
const CLOSE_MS = 180; // keep in step with --dur-sheet in index.css

function readUrl() {
  const p = new URLSearchParams(window.location.search);
  const mode = p.get("mode");
  const k = Number(p.get("k"));
  return {
    q: p.get("q") ?? "",
    mode: (MODES as string[]).includes(mode ?? "") ? (mode as Mode) : "hybrid",
    k: (KS as number[]).includes(k) ? (k as TopK) : (5 as TopK),
    view: p.get("view") === "bench" ? ("bench" as View) : ("search" as View),
    src: p.get("src"),
  };
}

const messageOf = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function App() {
  const initial = useMemo(readUrl, []);
  const [view, setView] = useState<View>(initial.view);
  const [mode, setMode] = useState<Mode>(initial.mode);
  const [k, setK] = useState<TopK>(initial.k);
  const [text, setText] = useState(initial.q);
  const [query, setQuery] = useState(initial.q);
  const [status, setStatus] = useState<Status | null>(null);
  const [pool, setPool] = useState<SearchResult | null>(null);
  const [scope, setScope] = useState<string | null>(initial.src);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The Words + meaning ranking for the current query: the one fixed baseline every rank shift is measured against.
  const [baseline, setBaseline] = useState<{ query: string; hits: Hit[] } | null>(null);
  const [active, setActive] = useState(0);
  const [reading, setReading] = useState<Hit | null>(null);
  const [readerVisible, setReaderVisible] = useState(false);
  const [help, setHelp] = useState(false);
  const [helpVisible, setHelpVisible] = useState(false);
  const [retry, setRetry] = useState(0);
  // Said when a mode switch dropped the Source filter because that file has no passages in the new mode.
  const [scopeNote, setScopeNote] = useState<string | null>(null);
  // What a keyboard move through the results should say aloud.
  const [moved, setMoved] = useState("");
  const [bench, setBench] = useState<Benchmark | null>(null);
  const [benchError, setBenchError] = useState<string | null>(null);

  const input = useRef<HTMLTextAreaElement>(null);
  // Set by an Esc that left the search box with a query in it: a second Esc right after clears it.
  const escArmed = useRef(false);
  const list = useRef<HTMLOListElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const helpOpener = useRef<HTMLElement | null>(null);
  const activeQuery = useRef<string | null>(null);

  // Status, and a first query so the page opens on real content.
  useEffect(() => {
    api
      .status()
      .then((s) => {
        setStatus(s);
        // Open on real content, but only in Search: Benchmarks should not run a search behind the table.
        if (!initial.q && initial.view === "search" && s.examples[0]) {
          setText(s.examples[0]);
          setQuery(s.examples[0]);
        }
      })
      .catch((e: unknown) => setError(messageOf(e)));
  }, [initial.q, initial.view]);

  const loadBench = useCallback(() => {
    setBenchError(null);
    api.benchmark().then(setBench).catch((e: unknown) => setBenchError(messageOf(e)));
  }, []);

  // Query, mode, K and view are deep-linkable.
  useEffect(() => {
    const p = new URLSearchParams();
    if (query) p.set("q", query);
    if (mode !== "hybrid") p.set("mode", mode);
    if (k !== 5) p.set("k", String(k));
    if (scope) p.set("src", scope);
    if (view === "bench") p.set("view", "bench");
    const qs = p.toString();
    history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
    document.title = view === "bench" ? "Benchmarks — sift" : query ? `${query} — sift` : "sift";
  }, [query, mode, k, view, scope]);

  // A Source filter belongs to the search it was chosen in: a new query starts from all files.
  const lastQuery = useRef(query);
  useEffect(() => {
    if (query === lastQuery.current) return;
    lastQuery.current = query;
    setScope(null);
    setScopeNote(null);
  }, [query]);

  const pickScope = useCallback((path: string | null) => {
    setScope(path);
    setScopeNote(null);
  }, []);

  // Typing commits to a query after a short pause.
  useEffect(() => {
    if (text.trim() === query) return;
    const t = window.setTimeout(() => setQuery(text.trim()), 320);
    return () => window.clearTimeout(t);
  }, [text, query]);

  useEffect(() => {
    if (!query) {
      setPool(null);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    api
      .search(query, mode, POOL, controller.signal)
      .then((res) => {
        setPool(res);
        if (mode === "hybrid" && !res.preview_miss) setBaseline({ query, hits: res.hits });
        setError(null);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(messageOf(e));
        setLoading(false);
      });
    return () => controller.abort();
  }, [query, mode, retry]);

  // The pool, scoped to one note if asked, cut to Show, and renumbered from 01.
  const scopedHits = useMemo(() => (pool && !pool.preview_miss ? pool.hits.filter((h) => !scope || h.file_path === scope) : []), [pool, scope]);
  const ranked = useMemo(() => scopedHits.map((h, i) => ({ ...h, rank: i + 1 })), [scopedHits]);
  const result = useMemo<SearchResult | null>(() => (pool ? { ...pool, hits: ranked.slice(0, k) } : null), [pool, ranked, k]);

  // The files the retrieved candidates come from, with how many chunks of each matched.
  const sources = useMemo(() => {
    const counts = new Map<string, number>();
    for (const h of pool && !pool.preview_miss ? pool.hits : []) counts.set(h.file_path, (counts.get(h.file_path) ?? 0) + 1);
    if (scope && !counts.has(scope)) counts.set(scope, 0);
    return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [pool, scope]);

  // A file chosen in one mode may have nothing in another. Rather than strand the searcher on an empty
  // list, go back to all files and say why.
  useEffect(() => {
    if (!scope || !pool || pool.preview_miss || pool.hits.some((h) => h.file_path === scope)) return;
    setScope(null);
    setScopeNote(`${scope.split("/").at(-1)} has no passages by ${MODE_LABEL[pool.mode].toLowerCase()}, so all files are shown.`);
  }, [pool, scope]);

  // Words and Meaning are measured against Words + meaning, the same baseline on every visit. Opened
  // straight into another mode, the baseline is fetched quietly; if that fails the reader simply omits it.
  useEffect(() => {
    if (!query || mode === "hybrid" || baseline?.query === query) return;
    const controller = new AbortController();
    api
      .search(query, "hybrid", POOL, controller.signal)
      .then((res) => !res.preview_miss && setBaseline({ query, hits: res.hits }))
      .catch(() => {});
    return () => controller.abort();
  }, [query, mode, baseline?.query]);

  // Ranked within the same Source scope as the list, so a scoped list compares like with like.
  const baselineRanks = useMemo(() => {
    if (!pool || pool.preview_miss || pool.mode === "hybrid" || baseline?.query !== pool.query) return null;
    return rankMap(baseline.hits.filter((h) => !scope || h.file_path === scope).map((h, i) => ({ ...h, rank: i + 1 })));
  }, [pool, baseline, scope]);

  // A new query starts at the top; the same query keeps its place, within the new length.
  useEffect(() => {
    if (!result) return;
    const same = activeQuery.current === result.query;
    activeQuery.current = result.query;
    setActive((i) => (same ? Math.min(i, Math.max(result.hits.length - 1, 0)) : 0));
  }, [result]);

  const open = useCallback((hit: Hit, trigger: HTMLElement | null) => {
    opener.current = trigger;
    setReading(hit);
    requestAnimationFrame(() => setReaderVisible(true));
  }, []);

  const close = useCallback(() => {
    setReaderVisible(false);
    window.setTimeout(() => setReading(null), CLOSE_MS);
  }, []);

  const closeHelp = useCallback(() => {
    setHelpVisible(false);
    window.setTimeout(() => {
      setHelp(false);
      helpOpener.current?.focus({ preventScroll: true });
    }, 150);
  }, []);

  const toggleHelp = useCallback(() => {
    if (help && helpVisible) {
      closeHelp();
      return;
    }
    helpOpener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setHelp(true);
    requestAnimationFrame(() => setHelpVisible(true));
  }, [help, helpVisible, closeHelp]);

  // Any click between the two Esc presses means the second is not "again".
  useEffect(() => {
    const reset = () => (escArmed.current = false);
    window.addEventListener("pointerdown", reset);
    return () => window.removeEventListener("pointerdown", reset);
  }, []);

  // The workspace is inert while reading, so focus can only return once it is live again.
  useEffect(() => {
    if (!reading) opener.current?.focus({ preventScroll: true });
  }, [reading]);

  const hits = result?.hits ?? [];

  // Shift+J / Shift+K in the reader: the next or previous result, in the order of the list behind it.
  const step = useCallback(
    (delta: 1 | -1) => {
      if (!reading) return;
      const at = hits.findIndex((h) => h.chunk_id === reading.chunk_id);
      const next = at + delta;
      if (at < 0 || next < 0 || next >= hits.length) return;
      setReading(hits[next]!);
      setActive(next);
    },
    [reading, hits],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // A widget (radio group, tab list) already took this key. The search field's own Enter handler only
      // stops the newline, so its Enter still reaches here to open the active result.
      if (event.defaultPrevented && event.target !== input.current) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      const typing = !!target?.matches("input, textarea, select, [contenteditable='true']");

      // The keymap sheet comes first: it closes before anything underneath it does.
      if (event.key === "Escape" && help && helpVisible) {
        event.preventDefault();
        closeHelp();
        return;
      }
      if (event.key === "?" && !typing) {
        event.preventDefault();
        toggleHelp();
        return;
      }
      if (reading) {
        if (event.key === "Escape") {
          event.preventDefault();
          close();
        }
        return;
      }
      // Esc is gentle with a half-remembered phrase: the first leaves the search box and keeps the query
      // and its results; pressed again straight away, it clears the search and puts you back in the box.
      if (event.key === "Escape" && target === input.current) {
        event.preventDefault();
        input.current?.blur();
        escArmed.current = !!text;
        return;
      }
      if (event.key === "Escape" && escArmed.current && !typing) {
        event.preventDefault();
        escArmed.current = false;
        setText("");
        setQuery("");
        input.current?.focus();
        return;
      }
      escArmed.current = false;
      if (event.key === "/" && !typing) {
        event.preventDefault();
        setView("search");
        window.setTimeout(() => input.current?.focus(), 0);
        return;
      }
      if (view !== "search" || !hits.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const next = Math.max(0, Math.min(hits.length - 1, active + (event.key === "ArrowDown" ? 1 : -1)));
        setActive(next);
        const h = hits[next];
        if (h && pool) {
          const why = evidence(h, queryTerms(pool.query), pool.mode, pool.query, hits);
          setMoved(`Result ${next + 1} of ${hits.length}: ${sectionOf(h.heading_context).title || h.file_path.split("/").at(-1)}${why ? `. ${why}` : ""}`);
        }
        if (target && !typing) {
          // Keep the focus ring on the row that is now active.
          window.setTimeout(() => list.current?.querySelector<HTMLElement>("[data-active] button.title")?.focus({ preventScroll: true }), 0);
        }
      } else if (event.key === "Enter" && (target === input.current || target === document.body)) {
        if (target === input.current && text.trim() !== query) return; // the field commits the new query
        event.preventDefault();
        const hit = hits[active];
        if (hit) open(hit, list.current?.querySelector<HTMLElement>("[data-active] button.title") ?? input.current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reading, close, view, hits, pool, active, open, text, query, help, helpVisible, closeHelp, toggleHelp]);

  const clear = () => {
    setText("");
    setQuery("");
    input.current?.focus();
  };

  const commit = (q: string) => {
    setText(q);
    setQuery(q.trim());
  };

  const terms = useMemo(() => queryTerms(pool?.query ?? query), [pool?.query, query]);

  const searchSource = (pool?.source ?? status?.search_source) === "live" ? "live" : pool || status ? "fixture" : null;
  const benchSource = bench ? (bench.source === "benchmark" ? "live" : "fixture") : null;
  const source = view === "bench" ? benchSource : searchSource;

  return (
    <div className="app">
      <a className="skip" href="#main">
        Skip to results
      </a>
      <div className="workspace" inert={reading !== null}>
        <Topbar corpus={status?.corpus ?? null} view={view} onView={setView} source={source} onHelp={toggleHelp} helpOpen={help && helpVisible} />
        <main id="main" tabIndex={-1}>
          <p className="visually-hidden" role="status">
            {loading
              ? "Searching…"
              : hits.length
                ? `${hits.length} results for “${pool?.query}”, ranked by ${MODE_LABEL[pool?.mode ?? mode].toLowerCase()}${scopeNote ? `. ${scopeNote}` : ""}`
                : pool?.preview_miss
                  ? `No recorded results for “${pool.query}” in the sample data.`
                  : pool && query
                    ? `Nothing matched “${pool.query}”${scope ? ` in ${scope.split("/").at(-1)}` : ""}.`
                    : ""}
          </p>
          <p className="visually-hidden" aria-live="polite">
            {moved}
          </p>
          <section id="panel-search" role="tabpanel" aria-labelledby="tab-search" className="board" hidden={view !== "search"}>
            <h1 className="visually-hidden">Search</h1>
            <Controls
              text={text}
              inputRef={input}
              onText={setText}
              onSubmit={() => setQuery(text.trim())}
              query={query}
              examples={status?.examples ?? []}
              onExample={commit}
              mode={mode}
              onMode={setMode}
              k={k}
              onK={setK}
              sources={sources}
              scope={scope}
              onScope={pickScope}
              miss={searchSource === "fixture" && (!query || !!pool?.preview_miss)}
              onClear={clear}
              timings={result?.timings_ms ?? null}
              fixture={searchSource === "fixture"}
              hasResults={hits.length > 0}
              indexRun={status?.index_run ?? null}
            />
            <Results
              result={result}
              query={query}
              loading={loading}
              error={error}
              terms={terms}
              activeIndex={active}
              onActive={setActive}
              onOpen={open}
              onRetry={() => setRetry((n) => n + 1)}
              onExample={commit}
              examples={status?.examples ?? []}
              scope={scope}
              onClearScope={() => pickScope(null)}
              scopeNote={scopeNote}
              sample={searchSource === "fixture"}
              listRef={list}
              total={scopedHits.length}
            />
          </section>
          <section id="panel-bench" role="tabpanel" aria-labelledby="tab-bench" className="board board-bench" hidden={view !== "bench"}>
            {view === "bench" && <Benchmarks bench={bench} error={benchError} load={loadBench} />}
          </section>
        </main>
        <footer className="colophon">
          <p>
            sift <span className="sep" aria-hidden="true">·</span> Runs on this machine
          </p>
          <div className="colophon-end">
            <button type="button" className="keyhint" onClick={toggleHelp} aria-haspopup="dialog" aria-expanded={help && helpVisible}>
              <kbd aria-hidden="true">?</kbd> Shortcuts
            </button>
          </div>
        </footer>
      </div>
      {help && <Keymap visible={helpVisible} onClose={closeHelp} />}
      {reading && <Reader hit={reading} mode={result?.mode ?? mode} was={baselineRanks ? (baselineRanks.get(reading.chunk_id) ?? "new") : null} found={evidence(reading, terms, result?.mode ?? mode, pool?.query ?? query, hits)} position={{ at: hits.findIndex((h) => h.chunk_id === reading.chunk_id), of: hits.length }} onStep={step} terms={terms} visible={readerVisible} onClose={close} />}
    </div>
  );
}
