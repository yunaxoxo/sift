import { fmtInt } from "../lib/format";
import { rovingKeys } from "../lib/roving";
import { Mark } from "./Mark";
import type { Corpus, View } from "../types";

interface Props {
  corpus: Corpus | null;
  view: View;
  onView: (view: View) => void;
  source: "live" | "fixture" | null;
  onHelp: () => void;
  helpOpen: boolean;
  /** A chosen collection: the counts here still cover every collection, so they say so. */
  collection: string | null;
}

const VIEWS: { id: View; label: string }[] = [
  { id: "search", label: "Search" },
  { id: "bench", label: "Benchmarks" },
];

export function Topbar({ corpus, view, onView, source, onHelp, helpOpen, collection }: Props) {
  return (
    <header className="topbar">
      <div className="topbar-id">
        <span className="brand">
          <Mark />
          <span className="wordmark" translate="no">
            sift
          </span>
        </span>
        <p className="corpus">
          {corpus ? (
            <>
              <span className="num">{fmtInt(corpus.files)}</span> files <span aria-hidden="true">·</span>{" "}
              <span className="num">{fmtInt(corpus.chunks)}</span> passages<span className="long"> indexed</span>
              {collection && " across all collections"}
            </>
          ) : (
            "Reading the index"
          )}
          {source && (
            <>
              {" "}
              <span aria-hidden="true">·</span> <span className="source">{source === "live" ? "Live index" : "Sample data"}</span>
            </>
          )}
        </p>
      </div>
      <div className="top-right">
        <div className="views" role="tablist" aria-label="View" onKeyDown={rovingKeys("[role=tab]", "horizontal")}>
          {VIEWS.map(({ id, label }) => (
            <button
              key={id}
              id={`tab-${id}`}
              role="tab"
              type="button"
              aria-selected={view === id}
              tabIndex={view === id ? 0 : -1}
              aria-controls={`panel-${id}`}
              onClick={() => onView(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <button type="button" className="keyhint" onClick={onHelp} aria-haspopup="dialog" aria-expanded={helpOpen} aria-label="Keyboard shortcuts">
          <kbd aria-hidden="true">?</kbd>
        </button>
      </div>
    </header>
  );
}
