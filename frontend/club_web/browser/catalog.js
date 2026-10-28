const MAX_PROGRAMS = 3;
let restoreCatalog = null;
window.addEventListener("popstate", () => restoreCatalog?.());

function node(tag, text, attributes = {}) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  for (const [key, value] of Object.entries(attributes)) result.setAttribute(key, value);
  return result;
}

export function hidePastStarts(root) {
  const rows = [...root.querySelectorAll("[data-program-start]")];
  if (!rows.length) return;
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  rows.forEach(row => { row.hidden = row.dataset.programStart < today; });
  const empty = root.querySelector("[data-starts-empty]");
  if (empty) empty.hidden = rows.some(row => !row.hidden);
}

export function prepareCatalog(url, notice) {
  const root = document.querySelector("#main");
  const source = root?.querySelector("#program-data");
  if (!source) { restoreCatalog = null; return; }
  if (root.dataset.catalogReady) return;
  root.dataset.catalogReady = "true";
  const programs = JSON.parse(source.textContent);
  const bySlug = new Map(programs.map(program => [program.slug, program]));
  const byId = new Map(programs.map(program => [program.id, program]));
  const bar = root.querySelector("#program-compare");
  const dialog = root.querySelector("#program-compare-dialog");
  let selected = new Set();
  let trigger = null;
  hidePastStarts(root);

  function render() {
    bar.hidden = !selected.size;
    root.querySelectorAll("[data-action='compare']").forEach(button => button.setAttribute("aria-pressed", String(selected.has(byId.get(button.dataset.id)?.slug))));
    bar.querySelector("[data-compare-count]").textContent = "Выбрано " + selected.size + " из " + MAX_PROGRAMS;
    const chips = bar.querySelector("[data-compare-selected]");
    chips.replaceChildren();
    for (const slug of selected) chips.append(node("button", bySlug.get(slug).title + " ×", { type: "button", "data-compare-remove": slug, "aria-label": "Убрать из сравнения: " + bySlug.get(slug).title }));
    root.querySelectorAll("input[name='compare']").forEach(input => { input.value = [...selected].join(","); });
  }

  function restore() {
    const raw = new URLSearchParams(location.search).get("compare") || "";
    selected = new Set();
    for (const slug of raw.split(",")) {
      if (bySlug.has(slug)) selected.add(slug);
      if (selected.size === MAX_PROGRAMS) break;
    }
    render();
  }

  function persist() {
    const next = new URL(location.href);
    if (selected.size) next.searchParams.set("compare", [...selected].join(","));
    else next.searchParams.delete("compare");
    history.replaceState(null, "", next);
    render();
    window.dispatchEvent(new PopStateEvent("popstate"));
  }

  function open(button) {
    const rows = [...selected].map(slug => bySlug.get(slug));
    if (!rows.length) { notice("Выберите хотя бы одну программу."); return; }
    trigger = button;
    const head = node("tr");
    head.append(node("th", "Параметр", { scope: "col" }));
    for (const program of rows) {
      const cell = node("th", undefined, { scope: "col" });
      cell.append(node("a", program.title, { href: url("dpo/" + program.slug) }));
      head.append(cell);
    }
    dialog.querySelector("thead").replaceChildren(head);
    const body = dialog.querySelector("tbody");
    body.replaceChildren();
    for (const [key, label] of [["direction", "Направление"], ["document", "Документ"], ["format_label", "Формат"], ["duration", "Длительность"], ["start_label", "Начало"], ["price_label", "Стоимость"], ["enrollment", "Набор"]]) {
      const row = node("tr");
      row.append(node("th", label, { scope: "row" }));
      for (const program of rows) row.append(node("td", key === "enrollment" ? ({ actual: "Набор открыт", nonactual: "Набор закрыт" }[program[key]] || "Уточняется") : program[key] || "Не указано"));
      body.append(row);
    }
    dialog.querySelector("[data-compare-fallback]").hidden = true;
    dialog.querySelector("[data-compare-message]").textContent = "";
    dialog.showModal();
  }

  root.addEventListener("click", async event => {
    const button = event.target.closest("button");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "compare") {
      const slug = byId.get(button.dataset.id)?.slug;
      if (!slug) return;
      if (selected.has(slug)) selected.delete(slug);
      else if (selected.size < MAX_PROGRAMS) selected.add(slug);
      else { notice("Можно сравнить до трех программ. Уберите одну из выбранных."); return; }
      persist();
    } else if (action === "clear-compare") { selected.clear(); persist(); root.querySelector("[data-action='compare']")?.focus(); }
    else if (button.dataset.compareRemove) { selected.delete(button.dataset.compareRemove); persist(); (selected.size ? bar.querySelector("[data-action='show-compare']") : root.querySelector("[data-action='compare']"))?.focus(); }
    else if (action === "show-compare") open(button);
    else if (button.hasAttribute("data-compare-close")) dialog.close();
    else if (button.hasAttribute("data-compare-copy")) {
      const share = new URL(url("dpo/"), location.origin);
      share.searchParams.set("compare", [...selected].join(","));
      try { await navigator.clipboard.writeText(share.href); dialog.querySelector("[data-compare-message]").textContent = "Ссылка скопирована."; }
      catch {
        const fallback = dialog.querySelector("[data-compare-fallback]");
        fallback.hidden = false;
        const input = fallback.querySelector("input");
        input.value = share.href; input.focus(); input.select();
        dialog.querySelector("[data-compare-message]").textContent = "Скопируйте выделенную ссылку вручную.";
      }
    }
  });
  dialog.addEventListener("close", () => { if (trigger?.isConnected) trigger.focus(); });
  restoreCatalog = restore;
  restore();
}
