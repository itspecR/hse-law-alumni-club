const UNSAVED = "Есть несохраненные изменения";
const CLOSED = "nonactual";
const EDITING_ACTIONS = ["module-add", "module-remove", "module-up", "module-down"].map(action => `[data-action="${action}"]`).join(",");

function selected(form, name) {
  const select = form.elements.namedItem(name);
  return select?.selectedOptions[0]?.textContent.trim() || "";
}

function value(form, name) {
  return form.elements.namedItem(name)?.value.trim() || "";
}

function rubles(amount) {
  return new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: Number.isInteger(amount) ? 0 : 2 }).format(amount);
}

function slot(preview, name) {
  return preview.querySelector(`[data-preview="${name}"]`);
}

function renderPreview(form, preview) {
  const title = slot(preview, "title");
  title.textContent = value(form, "title") || title.dataset.empty;
  slot(preview, "direction").textContent = value(form, "direction");
  const format = selected(form, "format");
  slot(preview, "facts").textContent = [format && format[0].toUpperCase() + format.slice(1), value(form, "duration")].filter(Boolean).join(" · ");
  const price = slot(preview, "price");
  const amount = Number(value(form, "price"));
  price.textContent = amount > 0 ? rubles(amount) : price.dataset.pending;
  const enrollment = slot(preview, "enrollment");
  enrollment.textContent = selected(form, "enrollment");
  enrollment.classList.toggle("program-status--open", form.elements.namedItem("enrollment")?.value !== CLOSED);
}

function markBlocks(root) {
  const links = [...root.querySelectorAll("[data-block-link]")];
  const observer = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).map(entry => entry.target.id);
    if (!visible.length) return;
    links.forEach(link => {
      if (link.hash === "#" + visible[0]) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
  }, { rootMargin: "-20% 0px -70% 0px" });
  root.querySelectorAll(".program-editor__block").forEach(block => observer.observe(block));
}

export function prepareProgramEditor() {
  const root = document.querySelector("[data-program-editor]");
  if (!root || root.dataset.editorReady) return;
  root.dataset.editorReady = "true";
  const form = root.querySelector("form");
  const preview = root.querySelector("[data-program-preview]");
  const state = root.querySelector("[data-program-state]");
  const changed = () => {
    renderPreview(form, preview);
    state.textContent = UNSAVED;
    state.dataset.dirty = "true";
  };
  form.addEventListener("input", changed);
  form.addEventListener("change", changed);
  form.addEventListener("click", event => { if (event.target.closest(EDITING_ACTIONS)) changed(); });
  if ("IntersectionObserver" in window) markBlocks(root);
}
