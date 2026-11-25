import { Fragment, useEffect, useRef } from "react";

interface Row {
  keys: string[];
  /** Two keys that do the same thing read "J or ↓"; a chord would read "J ↓". */
  either?: boolean;
  text: string;
  note?: string;
}

const ROWS: Row[] = [
  { keys: ["/"], text: "Focus search input" },
  { keys: ["↑", "↓"], either: true, text: "Move through search results" },
  { keys: ["Enter"], text: "Open the selected note" },
  { keys: ["J"], text: "Next highlighted match", note: "in an open note; wraps around" },
  { keys: ["K"], text: "Previous highlighted match", note: "in an open note; wraps around" },
  { keys: ["Shift", "J"], text: "Next result", note: "in an open note" },
  { keys: ["Shift", "K"], text: "Previous result", note: "in an open note" },
  { keys: ["Esc"], text: "Close the note, or leave the search box", note: "pressed twice, clears the search" },
  { keys: ["?"], text: "Toggle this reference" },
];

interface Props {
  /** False while the sheet is fading out. */
  visible: boolean;
  onClose: () => void;
}

/** A printed index of the keyboard: a quiet sheet, not a modal. The page behind stays live. */
export function Keymap({ visible, onClose }: Props) {
  const sheet = useRef<HTMLElement>(null);

  useEffect(() => {
    sheet.current?.focus({ preventScroll: true });
  }, []);

  // Clicking anywhere else dismisses it; the triggers toggle it themselves.
  useEffect(() => {
    const onDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (target && !sheet.current?.contains(target) && !target.closest(".keyhint")) onClose();
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [onClose]);

  return (
    <section ref={sheet} className="keymap" data-visible={visible || undefined} role="dialog" aria-modal="false" aria-labelledby="keymap-title" tabIndex={-1}>
      <header>
        <h2 id="keymap-title">Keyboard shortcuts</h2>
        <button type="button" className="close" onClick={onClose}>
          Close <kbd>Esc</kbd>
        </button>
      </header>
      <dl>
        {ROWS.map((row) => (
          <div key={row.text}>
            <dt>
              {row.keys.map((k, i) => (
                <Fragment key={k}>
                  {i > 0 && row.either && <span className="or">or</span>}
                  <kbd>{k}</kbd>
                </Fragment>
              ))}
            </dt>
            <dd>
              {row.text}
              {row.note && <span className="note-in">{row.note}</span>}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
