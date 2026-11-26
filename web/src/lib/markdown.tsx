import { Fragment, type ReactNode } from "react";
import { markClass, splitMatches, type Terms } from "./text";

// A small, safe Markdown renderer. It builds React elements directly (no HTML
// strings) and keeps each block's source line range, so the reader can mark
// the lines a search hit came from. It covers what engineering notes use:
// ATX headings, paragraphs, nested lists, GFM tables with alignment, fenced
// and indented code with language tags, block quotes (recursively), rules,
// YAML front matter, task lists, reference links, and inline code, emphasis,
// strikethrough, escapes and links.

export type Align = "left" | "center" | "right" | null;

export interface ListNode {
  text: string;
  children: ListBlock | null;
}
export interface ListBlock {
  ordered: boolean;
  items: ListNode[];
}

export type Block =
  | { kind: "heading"; level: number; text: string; start: number; end: number }
  | { kind: "paragraph"; text: string; start: number; end: number }
  | { kind: "list"; list: ListBlock; start: number; end: number }
  | { kind: "table"; head: string[]; align: Align[]; rows: string[][]; start: number; end: number }
  | { kind: "code"; lang: string; text: string; start: number; end: number }
  | { kind: "quote"; blocks: Block[]; start: number; end: number }
  | { kind: "rule"; start: number; end: number };

const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const QUOTE = /^\s{0,3}>/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const INDENT = /^( {4}|\t)/;
const SETEXT = /^\s{0,3}(=+|-+)\s*$/;
const REF_DEF = /^\s{0,3}\[([^\]^][^\]]*)\]:\s*<?([^\s>]+)>?(?:\s+["'(].*)?\s*$/;

const cells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());

const alignOf = (cell: string): Align => {
  const c = cell.trim();
  const left = c.startsWith(":");
  const right = c.endsWith(":");
  return left && right ? "center" : right ? "right" : left ? "left" : null;
};

const width = (indent: string) => indent.replace(/\t/g, "    ").length;

/** Nest list items by their indentation. */
function buildList(items: { indent: number; ordered: boolean; text: string }[]): ListBlock {
  const root: ListBlock = { ordered: items[0]!.ordered, items: [] };
  const stack: { indent: number; list: ListBlock }[] = [{ indent: items[0]!.indent, list: root }];
  for (const item of items) {
    while (stack.length > 1 && item.indent < stack.at(-1)!.indent) stack.pop();
    const top = stack.at(-1)!;
    if (item.indent > top.indent && top.list.items.length) {
      const parent = top.list.items.at(-1)!;
      const child: ListBlock = { ordered: item.ordered, items: [] };
      parent.children = child;
      stack.push({ indent: item.indent, list: child });
    }
    stack.at(-1)!.list.items.push({ text: item.text, children: null });
  }
  return root;
}

/** Inline `[text][id]` references become `[text](url)`; definition lines go blank so line numbers hold. */
function resolveReferences(lines: string[]): string[] {
  const defs = new Map<string, string>();
  let inFence = false;
  const kept = lines.map((line) => {
    if (FENCE.test(line)) inFence = !inFence;
    const def = !inFence && REF_DEF.exec(line);
    if (!def) return line;
    defs.set(def[1]!.toLowerCase(), def[2]!);
    return "";
  });
  if (!defs.size) return lines;
  inFence = false;
  return kept.map((line) => {
    if (FENCE.test(line)) inFence = !inFence;
    if (inFence) return line;
    return line.replace(/\[([^\]]+)\]\[([^\]]*)\]/g, (all, text: string, id: string) => {
      const url = defs.get((id || text).toLowerCase());
      return url ? `[${text}](${url})` : all;
    });
  });
}

/** `firstLine` is the 1-based line number of the first line of `source` in its file. */
export function parseMarkdown(source: string, firstLine = 1): Block[] {
  const lines = resolveReferences(source.replace(/\r\n?/g, "\n").split("\n"));
  const blocks: Block[] = [];
  let i = 0;
  const at = (n: number) => lines[n] ?? "";
  const no = (n: number) => n + firstLine;

  // YAML front matter at the very top of a whole file.
  if (firstLine === 1 && at(0).trim() === "---") {
    const close = lines.slice(1, 60).findIndex((l) => l.trim() === "---" || l.trim() === "...");
    if (close >= 0) {
      blocks.push({ kind: "code", lang: "yaml", text: lines.slice(1, close + 1).join("\n"), start: 1, end: close + 2 });
      i = close + 2;
    }
  }

  while (i < lines.length) {
    const line = at(i);
    if (!line.trim()) {
      i++;
      continue;
    }
    const start = no(i);

    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1]![0]!;
      const close = new RegExp(`^\\s{0,3}\\${marker}{3,}\\s*$`);
      const body: string[] = [];
      i++;
      while (i < lines.length && !close.test(at(i))) body.push(at(i++));
      i++;
      blocks.push({ kind: "code", lang: fence[2] ?? "", text: body.join("\n"), start, end: no(Math.min(i, lines.length) - 1) });
      continue;
    }
    if (INDENT.test(line) && !ITEM.test(line)) {
      const body: string[] = [];
      while (i < lines.length && (INDENT.test(at(i)) || (!at(i).trim() && INDENT.test(at(i + 1))))) {
        body.push(at(i++).replace(INDENT, ""));
      }
      blocks.push({ kind: "code", lang: "", text: body.join("\n"), start, end: no(i - 1) });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1]!.length, text: heading[2] ?? "", start, end: start });
      i++;
      continue;
    }
    if (RULE.test(line) && !ITEM.test(line)) {
      blocks.push({ kind: "rule", start, end: start });
      i++;
      continue;
    }
    if (line.includes("|") && TABLE_SEP.test(at(i + 1))) {
      const head = cells(line);
      const align = cells(at(i + 1)).map(alignOf);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && at(i).includes("|") && at(i).trim()) rows.push(cells(at(i++)));
      blocks.push({ kind: "table", head, align, rows, start, end: no(i - 1) });
      continue;
    }
    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && QUOTE.test(at(i))) body.push(at(i++).replace(/^\s{0,3}>\s?/, ""));
      blocks.push({ kind: "quote", blocks: parseMarkdown(body.join("\n"), start), start, end: no(i - 1) });
      continue;
    }
    if (ITEM.test(line)) {
      const items: { indent: number; ordered: boolean; text: string }[] = [];
      while (i < lines.length) {
        const m = ITEM.exec(at(i));
        if (m) {
          const indent = width(m[1]!);
          const ordered = /\d/.test(m[2]!);
          // A bullet list followed by a numbered one (or the reverse) is two lists.
          if (items.length && indent <= items[0]!.indent && ordered !== items[0]!.ordered) break;
          items.push({ indent, ordered, text: m[3] ?? "" });
          i++;
        } else if (!at(i).trim() && ITEM.test(at(i + 1))) {
          const n = ITEM.exec(at(i + 1))!;
          if (width(n[1]!) <= items[0]!.indent && /\d/.test(n[2]!) !== items[0]!.ordered) break;
          i++; // a blank line between items keeps the list going
        } else if (/^\s{2,}\S/.test(at(i)) && items.length) {
          items[items.length - 1]!.text += ` ${at(i).trim()}`;
          i++;
        } else break;
      }
      blocks.push({ kind: "list", list: buildList(items), start, end: no(i - 1) });
      continue;
    }
    if (SETEXT.test(at(i + 1)) && at(i + 1).trim()) {
      blocks.push({ kind: "heading", level: at(i + 1).trim()[0] === "=" ? 1 : 2, text: line.trim(), start, end: no(i + 1) });
      i += 2;
      continue;
    }
    const body: string[] = [];
    while (
      i < lines.length &&
      at(i).trim() &&
      !FENCE.test(at(i)) &&
      !HEADING.test(at(i)) &&
      !QUOTE.test(at(i)) &&
      !ITEM.test(at(i)) &&
      !(at(i).includes("|") && TABLE_SEP.test(at(i + 1)))
    ) {
      body.push(at(i++).trim());
    }
    blocks.push({ kind: "paragraph", text: body.join(" "), start, end: no(i - 1) });
  }
  return blocks;
}

const INLINE = new RegExp(
  [
    "(`[^`]+`)", // 1 code
    "(\\*\\*\\*[^*]+\\*\\*\\*)", // 2 bold italic
    "(\\*\\*[^*]+\\*\\*|__[^_]+__)", // 3 bold
    "(\\*(?!\\s)[^*\\n]*[^*\\s]\\*|(?<![\\w])_(?!\\s)[^_\\n]*[^_\\s]_(?![\\w]))", // 4 italic: a spaced `2 * 3 * 4` is not emphasis
    "(~~[^~]+~~)", // 5 strikethrough
    "(!?\\[[^\\]]*\\]\\([^)\\s]+\\))", // 6 link or image
    "(<https?://[^>\\s]+>)", // 7 <autolink>
    "(\\\\[\\\\`*_{}\\[\\]()#+\\-.!|~<>])", // 8 backslash escape
    "(https?://[^\\s<>)\\]]*[^\\s<>)\\].,;:!?'\"])", // 9 bare URL
  ].join("|"),
  "g",
);

function plain(text: string, terms: Terms, key = ((n) => () => n++)(0)): ReactNode[] {
  if (!terms.length) return [text];
  return splitMatches(text, terms).map((part) =>
    part.hit ? (
      <mark key={key()} className={markClass(part)} data-form={terms.forms?.has(part.text.toLowerCase()) || undefined}>
        {part.text}
      </mark>
    ) : (
      <Fragment key={key()}>{part.text}</Fragment>
    ),
  );
}

const isExternal = (href: string) => /^https?:\/\//i.test(href);

export function renderInline(text: string, terms: string[] = []): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  const next = () => key++;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(...plain(text.slice(last, m.index), terms, next));
    const token = m[0];
    if (m[1]) {
      out.push(
        <code key={key++} translate="no">
          {plain(token.slice(1, -1), terms)}
        </code>,
      );
    } else if (m[2]) out.push(<strong key={key++}><em>{renderInline(token.slice(3, -3), terms)}</em></strong>);
    else if (m[3]) out.push(<strong key={key++}>{renderInline(token.slice(2, -2), terms)}</strong>);
    else if (m[4]) out.push(<em key={key++}>{renderInline(token.slice(1, -1), terms)}</em>);
    else if (m[5]) out.push(<del key={key++}>{renderInline(token.slice(2, -2), terms)}</del>);
    else if (m[6]) {
      const parsed = /^(!?)\[([^\]]*)\]\(([^)\s]+)\)$/.exec(token);
      const label = parsed?.[2] ?? "";
      const href = parsed?.[3] ?? "";
      if (parsed?.[1]) out.push(<Fragment key={key++}>{label}</Fragment>); // an image: keep its description
      else if (isExternal(href)) {
        out.push(
          <a key={key++} href={href} target="_blank" rel="noopener noreferrer">
            {renderInline(label, terms)}
          </a>,
        );
      } else out.push(<Fragment key={key++}>{renderInline(label, terms)}</Fragment>);
    } else if (m[8]) out.push(<Fragment key={key++}>{token.slice(1)}</Fragment>);
    else {
      const href = m[7] ? token.slice(1, -1) : token;
      out.push(
        <a key={key++} href={href} target="_blank" rel="noopener noreferrer">
          {plain(href, terms)}
        </a>,
      );
    }
    last = m.index + token.length;
  }
  if (last < text.length) out.push(...plain(text.slice(last), terms, next));
  return out;
}

function renderList(list: ListBlock, terms: string[]): ReactNode {
  const Tag = list.ordered ? "ol" : "ul";
  return (
    <Tag>
      {list.items.map((item, i) => {
        const task = /^\[([ xX])\]\s+/.exec(item.text);
        return (
          <li key={i} className={task ? "task" : undefined}>
            {task && (
              <>
                <span className="box" data-done={task[1] !== " " || undefined} aria-hidden="true" />
                <span className="visually-hidden">{task[1] === " " ? "To do: " : "Done: "}</span>
              </>
            )}
            {renderInline(task ? item.text.slice(task[0].length) : item.text, terms)}
            {item.children && renderList(item.children, terms)}
          </li>
        );
      })}
    </Tag>
  );
}

/** `headingBase` shifts heading levels; `interactive: false` drops tab stops for blocks inside a clickable result. */
export function renderBlock(block: Block, terms: string[] = [], headingBase = 1, interactive = true): ReactNode {
  switch (block.kind) {
    case "heading": {
      const Tag = `h${Math.min(Math.max(block.level + headingBase - 1, 1), 6)}` as "h2";
      return <Tag>{renderInline(block.text, terms)}</Tag>;
    }
    case "paragraph":
      return <p>{renderInline(block.text, terms)}</p>;
    case "quote":
      return (
        <blockquote>
          {block.blocks.map((inner, i) => (
            <Fragment key={i}>{renderBlock(inner, terms, headingBase, interactive)}</Fragment>
          ))}
        </blockquote>
      );
    case "rule":
      return <hr />;
    case "code":
      return (
        <figure className="code">
          {block.lang && <figcaption translate="no">{block.lang}</figcaption>}
          <pre tabIndex={interactive ? 0 : undefined}>
            <code translate="no">{plain(block.text, terms)}</code>
          </pre>
        </figure>
      );
    case "list":
      return renderList(block.list, terms);
    case "table":
      return (
        <div className="md-table" tabIndex={interactive ? 0 : undefined} role={interactive ? "region" : undefined} aria-label={interactive ? "Table" : undefined}>
          <table>
            <thead>
              <tr>
                {block.head.map((c, i) => (
                  <th key={i} scope="col" style={block.align[i] ? { textAlign: block.align[i]! } : undefined}>
                    {renderInline(c, terms)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((c, i) => (
                    <td key={i} style={block.align[i] ? { textAlign: block.align[i]! } : undefined}>
                      {renderInline(c, terms)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}
