/**
 * The colophon: a pilcrow stamped on an ink tile. The two paper stems are the two rankers (words,
 * meaning); the forest bowl is the passage that came through. `public/favicon.svg` is the same stamp
 * on a 16px grid, `Mark` on a 20px one; each snaps every edge to a whole pixel, so neither blurs at 1x.
 * The tile is as tall as the wordmark's cap height, so it stands on the baseline with no offset.
 */
export function Mark() {
  return (
    <svg className="mark" viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">
      <rect className="tile" width="20" height="20" rx="4" />
      <path className="bowl" d="M11 4H8a4 4 0 0 0 0 8H11Z" />
      <path className="rule" d="M12 4V16M15 4V16" fill="none" strokeWidth="2" />
    </svg>
  );
}
