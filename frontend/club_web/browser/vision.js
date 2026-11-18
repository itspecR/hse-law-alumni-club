const SCHEMES = { bw: "черный на белом", wb: "белый на черном", bb: "синий на бежевом" };
const LABELS = {
  "vision-size": html => "Размер текста: " + Math.round(Number(html.style.getPropertyValue("--vis-zoom") || 1) * 100) + "%",
  "vision-scheme": html => "Цвета: " + SCHEMES[html.dataset.visScheme || "bw"],
  "vision-images": html => (html.classList.contains("vis-noimg") ? "Показать изображения" : "Скрыть изображения"),
};
const PRESSED = { "vision-serif": "vis-serif", "vision-spacing": "vis-spacing" };
const TOGGLES = ".club-header__tools [data-action='vision']";
const RETURN_TARGETS = [TOGGLES, "[data-action='menu']"];

function buttons(action) {
  return document.querySelectorAll(`[data-action='${action}']`);
}

function shown(node) {
  return Boolean(node?.getClientRects().length);
}

export function renderVision(html = document.documentElement) {
  for (const [action, label] of Object.entries(LABELS)) for (const button of buttons(action)) button.textContent = label(html);
  for (const [action, css] of Object.entries(PRESSED)) for (const button of buttons(action)) button.setAttribute("aria-pressed", String(html.classList.contains(css)));
  for (const button of document.querySelectorAll(TOGGLES)) button.setAttribute("aria-pressed", String(html.classList.contains("vis")));
}

export function focusVision(on) {
  if (on) { document.querySelector("#vision button")?.focus(); return; }
  RETURN_TARGETS.map(selector => document.querySelector(selector)).find(shown)?.focus();
}
