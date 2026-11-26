import { createPortal } from "react-dom";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { lineSpan } from "../lib/format";
import { parseMarkdown, renderBlock } from "../lib/markdown";
import { sectionOf, type Terms } from "../lib/text";
import type { Hit, Mode } from "../types";
import { Metrics } from "./Results";

interface Props {
  hit: Hit;
  /** The ranking mode the hit came from: decides which scores apply. */
  mode: Mode;
  /** Where the passage stood in the Words + meaning ranking (Words and Meaning only); "new" when it was not there. */
  was: number | "new" | null;
  /** The evidence line the list showed for this result, so the two always say the same thing. */
  found: string | null;
  /** Where this result sits in the list behind the reader. */
  position: { at: number; of: number };
  /** Open the next (1) or previous (-1) result of that list. */
  onStep: (delta: 1 | -1) => void;
  terms: string[];
  /** False while the panel is sliding out. */
  visible: boolean;
  onClose: () => void;
}

type Load = { state: "loading" } | { state: "ready"; text: string } | { state: "offline" };

export function Reader({ hit, mode, was, found, position, onStep, terms, visible, onClose }: Props) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const panel = useRef<HTMLElement>(null);
  const marked = useRef<HTMLDivElement | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const marks = useRef<HTMLElement[]>([]);
  const [nav, setNav] = useState({ count: 0, current: -1 });
  // J and K step through the matches in the matched chunk; "whole file" widens that to every match.
  const [wholeFile, setWholeFile] = useState(false);
  const [inFile, setInFile] = useState(0);
  const currentMark = useRef<HTMLElement | null>(null);
  // Where the J/K scope line renders: inside the matched chunk, which is built once per document.
  const [slot, setSlot] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoad({ state: "loading" });
    api
      .document(hit.file_path, controller.signal)
      .then((doc) => setLoad({ state: "ready", text: doc.text }))
      .catch((e: unknown) => {
        if (!(e instanceof DOMException && e.name === "AbortError")) setLoad({ state: "offline" });
      });
    return () => controller.abort();
  }, [hit.file_path]);

  useEffect(() => {
    panel.current?.focus({ preventScroll: true });
  }, []);

  const { source, firstLine } = useMemo(
    () => (load.state === "ready" ? { source: load.text, firstLine: 1 } : { source: hit.text, firstLine: hit.start_line }),
    [load, hit],
  );


  // The matched section is the run of blocks that overlap the hit's line span.
  const content = useMemo(() => {
    const blocks = parseMarkdown(source, firstLine);
    const out: ReactNode[] = [];
    let run: ReactNode[] = [];
    let first = true;
    const flush = () => {
      if (!run.length) return;
      out.push(
        <div key={`hit-${out.length}`} className="section-hit" ref={first ? marked : undefined}>
          {first && (
            <p className="match-tag num">
              Matched passage <span aria-hidden="true">·</span> {lineSpan(hit.start_line, hit.end_line)}
            </p>
          )}
          {first && found && <p className="found">{found}</p>}
          {first && <div ref={setSlot} />}
          {run}
          {first && (
            <details className="scores">
              <summary>Scores</summary>
              <Metrics hit={hit} mode={mode} was={was} />
            </details>
          )}
        </div>,
      );
      first = false;
      run = [];
    };
    blocks.forEach((block, i) => {
      // Word forms the analyzer matched (E.2) are marked too, but only in the lines they were found in.
      const forms = (hit.match_spans ?? []).filter((m) => block.end >= m.start_line && block.start <= m.end_line).map((m) => m.text.toLowerCase());
      // A typed word that is also a matched form stays a typed word.
      const marked: Terms = forms.length ? Object.assign([...terms, ...forms], { forms: new Set(forms.filter((f) => !terms.includes(f))) }) : terms;
      const node = <Fragment key={i}>{renderBlock(block, marked, 1)}</Fragment>;
      if (block.end >= hit.start_line && block.start <= hit.end_line) run.push(node);
      else {
        flush();
        out.push(node);
      }
    });
    flush();
    return out;
  }, [source, firstLine, hit, mode, was, found, terms]);

  useEffect(() => {
    if (load.state !== "loading") marked.current?.scrollIntoView({ block: "center" });
  }, [load.state, content]);

  // The highlighted occurrences J and K step through: the matched chunk's own, or the whole note's.
  // A chunk with none of its own falls back to the whole note, so the stepper is never dead while marks exist.
  useEffect(() => {
    const all = [...(body.current?.querySelectorAll<HTMLElement>("mark") ?? [])];
    const local = all.filter((m) => m.closest(".section-hit"));
    const list = wholeFile || !local.length ? all : local;
    // Widening or narrowing the scope keeps you on the same highlighted word when it is still in the list.
    const kept = currentMark.current ? list.indexOf(currentMark.current) : -1;
    marks.current = list;
    setInFile(all.length);
    setNav({ count: list.length, current: kept >= 0 ? kept : list.length ? Math.max(0, list.findIndex((m) => m.closest(".section-hit"))) : -1 });
    // load.state too: when the file turns out to be offline the content is unchanged but the DOM is not.
  }, [content, load.state, wholeFile]);

  useEffect(() => {
    marks.current.forEach((m, i) => m.toggleAttribute("data-current", i === nav.current));
    currentMark.current = marks.current[nav.current] ?? null;
  }, [nav]);

  const go = useCallback(
    (delta: 1 | -1) => {
      if (!nav.count) return;
      const next = (nav.current + delta + nav.count) % nav.count;
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      marks.current[next]?.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
      setNav({ count: nav.count, current: next });
    },
    [nav],
  );

  // J goes to the next match, K to the previous one; with Shift, to the next or previous result. The
  // arrow keys are left to scroll the note.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.target instanceof HTMLElement && event.target.matches("input, textarea, select")) return;
      const key = event.key.toLowerCase();
      if (key !== "j" && key !== "k") return;
      event.preventDefault();
      const delta = key === "j" ? 1 : -1;
      if (event.shiftKey) onStep(delta);
      else if (nav.count) go(delta);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, nav.count, onStep]);

  const scopeLine =
    inFile > 0 ? (
      <p className="match-scope">
        {wholeFile || nav.count === inFile
          ? `Stepping through all ${inFile} ${inFile === 1 ? "match" : "matches"} in this note.`
          : `Stepping through the ${nav.count} ${nav.count === 1 ? "match" : "matches"} in this passage.`}{" "}
        {nav.count !== inFile || wholeFile ? (
          <button type="button" className="link" onClick={() => setWholeFile((v) => !v)}>
            {wholeFile ? "Only this passage" : `All ${inFile} in the note`}
          </button>
        ) : null}
      </p>
    ) : null;

  const { title } = sectionOf(hit.heading_context);

  return (
    <div className="reader-layer" data-visible={visible || undefined}>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <article
        ref={panel}
        className="reader"
        role="dialog"
        aria-modal="true"
        aria-label={title || hit.file_path}
        tabIndex={-1}
      >
        <header>
          {/* What you opened: the section's own title, then the note it lives in. The full path is the tooltip. */}
          <p className="where">
            {title && <span className="doc-title">{title}</span>}
            <span className="file num" title={hit.file_path} translate="no">
              {hit.file_path.split("/").at(-1)}
            </span>
            {position.at >= 0 && (
              <span className="of num" aria-hidden="true">
                <span className="of-word">Result </span>
                {position.at + 1} of {position.of}
              </span>
            )}
          </p>
          {/* Said aloud when Shift+J / Shift+K moves to another result. */}
          <span className="visually-hidden" aria-live="polite">
            {position.at >= 0 ? `Result ${position.at + 1} of ${position.of}: ${title || hit.file_path}` : ""}
          </span>
          <div className="matchnav" role="group" aria-label="Matches in this note">
            <button type="button" onClick={() => go(-1)} disabled={!nav.count} aria-label="Previous match" aria-keyshortcuts="k">
              <span aria-hidden="true">↑</span>
            </button>
            <span className="count num" aria-hidden="true">
              {nav.count ? `Match ${nav.current + 1} of ${nav.count}` : "No matches"}
            </span>
            <span className="visually-hidden" aria-live="polite">
              {nav.count ? `Match ${nav.current + 1} of ${nav.count} ${nav.count === inFile ? "in this note" : "in this passage"}` : "No matches"}
            </span>
            <button type="button" onClick={() => go(1)} disabled={!nav.count} aria-label="Next match" aria-keyshortcuts="j">
              <span aria-hidden="true">↓</span>
            </button>
          </div>
          <button type="button" className="close" onClick={onClose}>
            <span className="wide">
              Close <kbd>Esc</kbd>
            </span>
            <span className="narrow">← Back to results</span>
          </button>
        </header>
        <div className="reader-body" ref={body}>
          {load.state === "offline" && (
            <p className="note muted">The full file isn’t available here, so this shows the matched section on its own.</p>
          )}
          {slot && scopeLine && createPortal(scopeLine, slot)}
          <div className="prose prose-reader">{load.state === "loading" ? <p className="muted">Opening file…</p> : content}</div>
        </div>
      </article>
    </div>
  );
}
