const INDENT = "    ";
const LINK_TEXT = "текст ссылки";
const LINK_URL = "https://";
const WRAPS = { bold: "**", italic: "*" };
const PREFIXES = { h2: () => "## ", h3: () => "### ", quote: () => "> ", bullets: () => "- ", numbers: index => index + 1 + ". " };
const BLOCK_MARK = /^(#{1,6} |> |[-*+] |\d+[.)] )/;
const LIST_LINE = /^\s*([-*+]|\d+[.)]) /;
const LEADING_INDENT = new RegExp("^ {1," + INDENT.length + "}");

function lineBounds(text, start, end) {
  const from = text.lastIndexOf("\n", start - 1) + 1;
  const stop = text.indexOf("\n", end);
  return [from, stop < 0 ? text.length : stop];
}

function put(area, from, to, value, selectFrom, selectTo) {
  area.setRangeText(value, from, to);
  area.setSelectionRange(selectFrom, selectTo);
  area.dispatchEvent(new Event("input", { bubbles: true }));
}

function wrap(area, mark) {
  const { selectionStart: start, selectionEnd: end, value } = area;
  const wrapped = value.slice(start - mark.length, start) === mark && value.slice(end, end + mark.length) === mark;
  if (wrapped) {
    put(area, start - mark.length, end + mark.length, value.slice(start, end), start - mark.length, end - mark.length);
    return;
  }
  put(area, start, end, mark + value.slice(start, end) + mark, start + mark.length, end + mark.length);
}

function editLines(area, change) {
  const [from, to] = lineBounds(area.value, area.selectionStart, area.selectionEnd);
  const lines = change(area.value.slice(from, to).split("\n"));
  const text = lines.join("\n");
  put(area, from, to, text, from, from + text.length);
}

function split(line) {
  const indent = line.match(/^\s*/)[0];
  return [indent, line.slice(indent.length)];
}

function prefix(area, kind) {
  const mark = PREFIXES[kind];
  editLines(area, lines => {
    const present = lines.every((line, index) => split(line)[1].startsWith(mark(index)));
    return lines.map((line, index) => {
      const [indent, body] = split(line);
      const plain = body.replace(BLOCK_MARK, "");
      return present ? indent + plain : indent + mark(index) + plain;
    });
  });
}

function shift(area, step) {
  editLines(area, lines => lines.map(line => (step > 0 ? INDENT + line : line.replace(LEADING_INDENT, ""))));
}

function link(area) {
  const { selectionStart: start, selectionEnd: end, value } = area;
  const text = value.slice(start, end) || LINK_TEXT;
  const urlStart = start + text.length + 3;
  put(area, start, end, "[" + text + "](" + LINK_URL + ")", urlStart, urlStart + LINK_URL.length);
}

function apply(area, kind) {
  if (WRAPS[kind]) wrap(area, WRAPS[kind]);
  else if (PREFIXES[kind]) prefix(area, kind);
  else if (kind === "indent") shift(area, 1);
  else if (kind === "outdent") shift(area, -1);
  else if (kind === "link") link(area);
}

export function formatAction(button) {
  const area = button.closest("[data-format-field]")?.querySelector("textarea[data-rich]");
  if (!area) return;
  area.focus();
  apply(area, button.dataset.format);
}

export function formatKey(event) {
  const area = event.target;
  if (event.key !== "Tab" || event.altKey || event.ctrlKey || event.metaKey || !area.matches?.("textarea[data-rich]")) return;
  const [from, to] = lineBounds(area.value, area.selectionStart, area.selectionEnd);
  if (!area.value.slice(from, to).split("\n").every(line => LIST_LINE.test(line))) return;
  event.preventDefault();
  shift(area, event.shiftKey ? -1 : 1);
}
