import type { KeyboardEvent } from "react";

/**
 * Arrow-key behaviour for a radio group or tab list: one tab stop, arrows move
 * between items and select as they go, Home and End jump to the ends.
 * `horizontal` leaves ↑ and ↓ alone (for tabs, so they can still move through results).
 */
export function rovingKeys(selector: string, axes: "both" | "horizontal" = "both") {
  return (event: KeyboardEvent<HTMLElement>) => {
    const items = [...event.currentTarget.querySelectorAll<HTMLElement>(selector)];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (i < 0 || event.metaKey || event.ctrlKey || event.altKey) return;
    const n = items.length;
    const vertical = axes === "both";
    let next: number;
    if (event.key === "ArrowRight" || (vertical && event.key === "ArrowDown")) next = (i + 1) % n;
    else if (event.key === "ArrowLeft" || (vertical && event.key === "ArrowUp")) next = (i - 1 + n) % n;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = n - 1;
    else return;
    event.preventDefault();
    items[next]?.focus();
    items[next]?.click();
  };
}
