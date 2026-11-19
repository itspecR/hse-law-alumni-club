const HOURS = /(\d+(?:[.,]\d+)?)/;

function inputs(row) {
  return [...row.querySelectorAll("[data-module-field]")];
}

function rows(editor) {
  return [...editor.querySelectorAll(":scope > [data-module]")];
}

function hours(row) {
  const match = HOURS.exec(row.querySelector('[data-module-field="hours"]').value);
  return match ? Number(match[1].replace(",", ".")) : null;
}

function title(row) {
  return row.querySelector('[data-module-summary="title"]').textContent;
}

function announce(editor, text) {
  editor.querySelector("[data-modules-status]").textContent = text;
}

function summarize(row, editor) {
  row.querySelector('[data-module-summary="title"]').textContent = inputs(row)[0].value.trim() || editor.dataset.modulesNew;
  row.querySelector('[data-module-summary="meta"]').textContent = [...row.querySelectorAll("[data-module-meta]")].map(input => input.value.trim()).filter(Boolean).join(" · ");
}

function hourParts(modules) {
  const known = modules.map(hours).filter(value => value !== null);
  const sum = known.reduce((result, value) => result + value, 0);
  const parts = known.length ? ["часов: " + sum.toLocaleString("ru-RU")] : [];
  if (known.length < modules.length && modules.length) parts.push("часы указаны не у всех модулей");
  return parts;
}

function total(editor) {
  const modules = rows(editor);
  const parts = [editor.dataset.modulesCount + ": " + modules.length];
  if (editor.hasAttribute("data-modules-hours")) parts.push(...hourParts(modules));
  editor.querySelector("[data-modules-total]").textContent = parts.join(" · ");
}

function refresh(editor) {
  const modules = rows(editor);
  modules.forEach((row, index) => {
    const up = row.querySelector('[data-action="module-up"]');
    const down = row.querySelector('[data-action="module-down"]');
    up.disabled = index === 0;
    down.disabled = index === modules.length - 1;
    up.setAttribute("aria-label", "Поднять «" + title(row) + "»");
    down.setAttribute("aria-label", "Опустить «" + title(row) + "»");
  });
  total(editor);
}

function toggle(row, open = !row.classList.contains("is-open")) {
  row.classList.toggle("is-open", open);
  row.querySelector('[data-action="module-toggle"]').setAttribute("aria-expanded", String(open));
  row.querySelector(".office-module__body").hidden = !open;
}

function add(editor, button) {
  const row = editor.querySelector("template").content.firstElementChild.cloneNode(true);
  button.before(row);
  toggle(row, true);
  row.querySelector("[data-module-field]").focus();
}

function move(row, step) {
  const sibling = step < 0 ? row.previousElementSibling : row.nextElementSibling;
  if (!sibling?.matches("[data-module]")) return;
  if (step < 0) sibling.before(row);
  else sibling.after(row);
}

function remove(editor, row) {
  const next = [row.nextElementSibling, row.previousElementSibling].find(node => node?.matches("[data-module]"));
  const name = title(row);
  row.remove();
  refresh(editor);
  (next?.querySelector('[data-action="module-toggle"]') || editor.querySelector('[data-action="module-add"]')).focus();
  announce(editor, "Удалено: «" + name + "».");
}

function shift(editor, row, button, step) {
  move(row, step);
  refresh(editor);
  const other = row.querySelector(step < 0 ? '[data-action="module-down"]' : '[data-action="module-up"]');
  (button.disabled ? other : button).focus();
  const modules = rows(editor);
  announce(editor, "«" + title(row) + "»: место " + (modules.indexOf(row) + 1) + " из " + modules.length + ".");
}

export function moduleAction(button, action) {
  const editor = button.closest("[data-modules]");
  const row = button.closest("[data-module]");
  if (action === "module-remove") return remove(editor, row);
  if (action === "module-up" || action === "module-down") return shift(editor, row, button, action === "module-up" ? -1 : 1);
  if (action === "module-add") add(editor, button);
  else if (action === "module-toggle") toggle(row);
  refresh(editor);
}

export function prepareCourse() {
  document.querySelectorAll("[data-modules]").forEach(editor => {
    if (editor.dataset.courseReady) return;
    editor.dataset.courseReady = "true";
    editor.addEventListener("input", event => {
      const row = event.target.closest("[data-module]");
      if (!row || editor.hasAttribute("data-modules-fixed")) return;
      summarize(row, editor);
      total(editor);
    });
    refresh(editor);
  });
}
