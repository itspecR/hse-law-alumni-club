const LOCK_CLASS = "menu-locked";
const DISMISSED_CLASS = "is-dismissed";
const MENUS = {
  menu: { panel: "#club-menu", background: "#page, .club-footer, .mobile-tabs, .club-header__brand", opened: "Закрыть меню", closed: "Открыть меню" },
  "office-menu": { panel: "#office-navigation", background: ".site-office-content, .office-brand", opened: "Закрыть разделы", closed: "Разделы панели" },
};
const OPEN_BUTTONS = Object.keys(MENUS).map(action => `[data-action='${action}'][aria-expanded='true']`).join(",");

function setOpen(button, open) {
  const menu = MENUS[button.dataset.action];
  const panel = document.querySelector(menu.panel);
  button.setAttribute("aria-expanded", String(open));
  button.setAttribute("aria-label", open ? menu.opened : menu.closed);
  panel.dataset.open = String(open);
  for (const node of document.querySelectorAll(menu.background)) node.inert = open;
  document.documentElement.classList.toggle(LOCK_CLASS, open);
  return panel;
}

function firstItem(panel) {
  return panel.querySelector("[aria-current='page']") || panel.querySelector("a, button");
}

export function toggleMenu(button) {
  const open = button.getAttribute("aria-expanded") !== "true";
  const panel = setOpen(button, open);
  if (open) firstItem(panel)?.focus();
  return open;
}

export function closeMenus() {
  for (const button of document.querySelectorAll(OPEN_BUTTONS)) {
    setOpen(button, false);
    button.focus();
  }
}

export function dismissLoginHint(target) {
  target.closest?.(".club-header__join")?.classList.add(DISMISSED_CLASS);
}

export function restoreLoginHint(event) {
  const join = event.currentTarget;
  if (!join.contains(event.relatedTarget)) join.classList.remove(DISMISSED_CLASS);
}
