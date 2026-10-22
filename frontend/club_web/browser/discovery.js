const normalized = value => String(value || "").toLocaleLowerCase("ru").replace(/ё/g, "е").trim();
let searchReady = false;
const CHANGES_RETURN = "club-changes-return";

function prepareSearch(url, mirror) {
  const dialog = document.querySelector("#search-dialog");
  if (!dialog || searchReady) return;
  searchReady = true;
  const form = dialog.querySelector("form");
  const input = form.elements.q;
  const results = dialog.querySelector("[data-search-results]");
  const status = dialog.querySelector("[data-search-status]");
  let controller;
  let timer;
  let trigger;
  let pendingDirection;
  let sequence = 0;
  function focusResult(direction) {
    const links = [...results.querySelectorAll("[data-search-row]")].filter(link => !link.hidden && link.getClientRects().length);
    if (!links.length) return false;
    const index = links.indexOf(document.activeElement);
    links[index < 0 ? (direction === "ArrowDown" ? 0 : links.length - 1) : (index + (direction === "ArrowDown" ? 1 : -1) + links.length) % links.length].focus();
    return true;
  }
  async function search() {
    clearTimeout(timer);
    controller?.abort();
    pendingDirection = null;
    const turn = ++sequence;
    const q = input.value.trim();
    results.replaceChildren();
    if (q.length < 2) { results.removeAttribute("aria-busy"); status.textContent = "Введите хотя бы две буквы."; return; }
    controller = new AbortController();
    status.textContent = "Ищем материалы…";
    results.setAttribute("aria-busy", "true");
    try {
      const endpoint = mirror ? "views/search.html" : "views/search?q=" + encodeURIComponent(q);
      const response = await fetch(url(endpoint), { signal: controller.signal });
      if (!response.ok) throw new Error();
      const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
      const groups = parsed.querySelector("[data-search-groups]");
      if (!groups || parsed.querySelector(".site-errors")) throw new Error();
      if (turn !== sequence || !dialog.open) return;
      results.append(document.importNode(groups, true));
      if (mirror) {
        results.querySelectorAll("[data-search-row]").forEach(row => { row.hidden = !normalized(q).split(/\s+/).every(word => normalized(row.dataset.searchText || row.textContent).includes(word)); });
        results.querySelectorAll("section,nav").forEach(group => { [...group.querySelectorAll("[data-search-row]")].filter(row => !row.hidden).slice(6).forEach(row => { row.hidden = true; }); group.hidden = ![...group.querySelectorAll("[data-search-row]")].some(row => !row.hidden); });
      }
      const count = [...results.querySelectorAll("[data-search-row]")].filter(row => !row.hidden).length;
      status.textContent = count ? "Найдено: " + count : "Ничего не найдено. Попробуйте другое слово.";
      if (pendingDirection && document.activeElement === input) focusResult(pendingDirection);
    } catch (error) {
      if (error.name !== "AbortError" && turn === sequence) status.textContent = "Не удалось выполнить поиск. Проверьте подключение и нажмите «Найти» снова.";
    } finally { if (turn === sequence) { results.removeAttribute("aria-busy"); pendingDirection = null; } }
  }
  document.addEventListener("click", event => {
    const anchor = event.target.closest("a[href]");
    if (!anchor || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const target = new URL(anchor.href);
    if (target.origin !== location.origin || target.pathname.replace(/\/$/, "") !== url("search").replace(/\/$/, "") || anchor.closest("#main")) return;
    event.preventDefault();
    trigger = anchor;
    dialog.showModal();
    input.focus();
  });
  form.addEventListener("submit", event => { event.preventDefault(); event.stopPropagation(); void search(); });
  input.addEventListener("input", () => { controller?.abort(); pendingDirection = null; sequence++; clearTimeout(timer); timer = setTimeout(search, 220); });
  dialog.querySelector("[data-search-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => { clearTimeout(timer); controller?.abort(); pendingDirection = null; sequence++; trigger?.focus(); });
  dialog.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dialog.close(); return; }
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    if (results.hasAttribute("aria-busy") && document.activeElement === input) {
      event.preventDefault();
      pendingDirection = event.key;
      return;
    }
    if (focusResult(event.key)) event.preventDefault();
  });
}

function rememberReadingItem(page) {
  const current = page.querySelector("[data-change-open][aria-current='page']");
  try {
    const saved = JSON.parse(sessionStorage.getItem(CHANGES_RETURN) || "null");
    if (saved && current) sessionStorage.setItem(CHANGES_RETURN, JSON.stringify({ ...saved, focus: current.dataset.changeOpen }));
  } catch {}
}

function prepareChanges(url) {
  const page = document.querySelector(".changes-page");
  if (!page || page.dataset.prepared) return;
  page.dataset.prepared = "true";
  const dialog = page.querySelector("[data-changes-filters]");
  const form = page.querySelector("[data-changes-form]");
  const home = page.querySelector("[data-changes-filter-home]");
  const open = page.querySelector("[data-changes-filter-open]");
  let original;
  if (dialog && form) {
    open.addEventListener("click", () => {
      original = [...form.elements].map(input => input.value);
      dialog.querySelector("[data-changes-filter-body]").append(form);
      dialog.showModal();
    });
    dialog.querySelector("[data-changes-filter-cancel]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => {
      [...form.elements].forEach((input, index) => { input.value = original[index]; });
      home.append(form);
      open.focus();
    });
    form.addEventListener("submit", () => {
      if (dialog.open && document.body.dataset.mirror === "true") {
        original = [...form.elements].map(input => input.value);
        dialog.close();
      }
    });
  }
  page.querySelectorAll("[data-change-open]").forEach(link => link.addEventListener("click", () => {
    try {
      const listURL = url("changes") + location.search;
      const previous = JSON.parse(sessionStorage.getItem(CHANGES_RETURN) || "null");
      sessionStorage.setItem(CHANGES_RETURN, JSON.stringify({ url: listURL, scroll: page.classList.contains("changes-page--reading") && previous?.url === listURL ? previous.scroll : scrollY, focus: link.dataset.changeOpen, unread: !!page.querySelector("[data-unread-filter]")?.checked }));
    } catch {}
  }));
  if (!page.classList.contains("changes-page--reading")) {
    try {
      const saved = JSON.parse(sessionStorage.getItem(CHANGES_RETURN) || "null");
      if (saved?.url?.replace(/\/(?=\?|$)/, "") === (location.pathname + location.search).replace(/\/(?=\?|$)/, "")) {
        sessionStorage.removeItem(CHANGES_RETURN);
        const link = [...page.querySelectorAll("[data-change-open]")].find(link => link.dataset.changeOpen === saved.focus);
        if (link) requestAnimationFrame(() => { link.focus({ preventScroll: true }); scrollTo(0, Number(saved.scroll) || 0); });
        const unread = page.querySelector("[data-unread-filter]");
        if (unread) unread.checked = !!saved.unread;
      }
    } catch {}
  } else rememberReadingItem(page);
  const copy = page.querySelector("[data-changes-copy]");
  copy?.addEventListener("click", async () => {
    const text = page.querySelector("[data-changes-text]").innerText;
    const status = page.querySelector("[data-changes-copy-status]");
    try { await navigator.clipboard.writeText(text); status.textContent = "Текст скопирован."; }
    catch {
      const field = page.querySelector("[data-changes-copy-field]");
      field.hidden = false;
      field.value = text;
      field.focus();
      field.select();
      status.textContent = "Копирование недоступно. Выделенный текст можно скопировать вручную.";
    }
  });
  page.querySelectorAll("[data-changes-back]").forEach(link => {
    if (document.body.dataset.mirror !== "true") return;
    const query = location.search;
    link.href = url("changes") + query;
  });
}

export function prepareDiscovery(url, mirror) {
  prepareSearch(url, mirror);
  prepareChanges(url);
}
