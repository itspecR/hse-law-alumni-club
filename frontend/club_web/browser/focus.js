const PAGE_HEADING = "main h1";
const SUBMIT_BUTTON = "button[type='submit'],button:not([type])";

function placeholder(node) {
  if (node && !node.hasAttribute("tabindex")) node.setAttribute("tabindex", "-1");
  return node;
}

function sameAction(mark) {
  return [...document.querySelectorAll("[data-api]")].filter(node => node.dataset.api === mark.api);
}

function counterpart(owner, mark) {
  if (owner.tagName !== "FORM") return owner;
  const field = mark.name && [...owner.elements].find(node => node.getAttribute("name") === mark.name);
  return field || owner.querySelector(SUBMIT_BUTTON);
}

function replacement(mark) {
  if (!mark.api) return null;
  return sameAction(mark).map(owner => counterpart(owner, mark)).find(Boolean) || null;
}

function lost() {
  const active = document.activeElement;
  return !active || active === document.body;
}

export function focusMark() {
  const active = document.activeElement;
  if (!active || active === document.body) return null;
  return { element: active, api: active.closest("[data-api]")?.dataset.api, name: active.getAttribute("name") };
}

export function restoreFocus(mark) {
  if (!mark || !lost()) return;
  const target = mark.element.isConnected ? mark.element : replacement(mark) || placeholder(document.querySelector(PAGE_HEADING));
  target?.focus();
}

export function focusHeading(container) {
  placeholder(container?.querySelector("h1,h2"))?.focus();
}
