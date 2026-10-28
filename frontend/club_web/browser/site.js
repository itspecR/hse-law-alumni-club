import { prepareMini } from "./mini.js";
import { prepareDiscovery } from "./discovery.js";
import { prepareCatalog } from "./catalog.js";
import { prepareHome } from "./home.js";
import { hideSkeleton, prepareSkeleton } from "./skeleton.js";

const base = document.body.dataset.base || "/";
const mirror = document.body.dataset.mirror === "true";
const memberKey = "club_token";
const officeKey = "club_admin_token";
const readingKey = "club-reading-v1";
const notNotifiedText = "Заявка сохранена, но учебный офис не получил уведомление. Сообщите номер заявки в поддержку.";
let pendingCart = 0;
let pendingCheckout = false;
let installPrompt = null;
let mirrorApi = null;
let readingPageSaved = false;
let removedReading = null;

function stored(key, fallback = null) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}

function store(key, value) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); return true; }
  catch { notice("Хранилище браузера недоступно. Проверьте настройки приватности."); return false; }
}

function url(path) {
  return base + path.replace(/^\//, "");
}

function currentPath() {
  return document.querySelector("#page")?.dataset.path || "/";
}

function token(office = currentPath().startsWith("/admin")) {
  return stored(office ? officeKey : memberKey);
}

function cartSession() {
  const key = stored("club_cart") || crypto.randomUUID();
  if (!store("club_cart", key)) throw new Error("Для корзины нужен доступ к хранилищу браузера. Разрешите его в настройках.");
  return key;
}

function notice(message) {
  const box = document.querySelector("#toast");
  if (!box) return;
  box.textContent = message;
  box.hidden = false;
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => { box.hidden = true; }, 6000);
}

function safeLink(value, payment = false) {
  try {
    const parsed = new URL(value, location.origin);
    if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password) return null;
    if (payment && parsed.protocol !== "https:") return null;
    return parsed.href;
  } catch { return null; }
}

async function request(path, method = "GET", body, extra = {}) {
  if (!navigator.onLine && method !== "GET") throw new Error("Нет подключения к интернету. Подключитесь и повторите.");
  if (mirror) {
    if (path === "/cart" && window.clubMirrorCart) return window.clubMirrorCart(method, body);
    if (method !== "GET") throw new Error("В демоверсии отправка и изменение данных отключены.");
    mirrorApi ||= await fetch(url("data/mirror-api.json")).then(response => response.json());
    const key = path.split("?")[0];
    if (!Object.hasOwn(mirrorApi, key)) throw new Error("Эти данные недоступны в демоверсии.");
    return structuredClone(mirrorApi[key]);
  }
  const headers = { accept: "application/json", ...extra };
  const session = token();
  if (session) headers.authorization = `Bearer ${session}`;
  if (path === "/cart" || path === "/orders") headers["x-cart-session"] = cartSession();
  if (body !== undefined && !(body instanceof FormData)) headers["content-type"] = "application/json";
  let response;
  try {
    response = await fetch(url("api" + path), { method, headers, body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body) });
  } catch { throw new Error("Нет соединения с сервером. Проверьте интернет и повторите."); }
  let data;
  try { data = await response.json(); } catch { throw new Error("Не удалось получить ответ. Попробуйте позже."); }
  if (!response.ok) {
    if (response.status === 401 && session) {
      store(currentPath().startsWith("/admin") ? officeKey : memberKey, null);
      notice("Сессия завершилась. Войдите снова.");
      if (currentPath().startsWith("/admin") || currentPath().startsWith("/lk")) void refresh();
    }
    throw new Error(data.error || "Не удалось выполнить действие. Попробуйте позже.");
  }
  return data;
}

function element(tag, text, attributes = {}) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

async function copyText(text, trigger) {
  try {
    await navigator.clipboard.writeText(text);
    notice("Скопировано");
  } catch {
    const dialog = document.querySelector("#site-dialog");
    const content = document.querySelector("#dialog-content");
    const field = element("input", undefined, { type: "text", value: text, readonly: "", "aria-label": "Текст для копирования" });
    content.replaceChildren(element("h2", "Копирование"), element("p", "Браузер запретил автоматическое копирование. Скопируйте выделенный текст вручную."), field);
    dialog.addEventListener("close", () => { content.replaceChildren(); trigger.focus(); }, { once: true });
    dialog.showModal();
    field.focus();
    field.select();
  }
}

function formBody(form) {
  const body = {};
  for (const input of form.elements) {
    if (!input.name || input.disabled || input.type === "submit") continue;
    let value = input.value;
    if (input.type === "radio") {
      if (input.checked) body[input.name] = value;
      continue;
    }
    if (input.type === "checkbox") {
      if (input.name === "interests") { body.interests ||= []; if (input.checked) body.interests.push(value); continue; }
      if (input.hasAttribute("data-list")) { body[input.name] ||= []; if (input.checked) body[input.name].push(value); continue; }
      value = input.checked;
    } else if (input.type === "number") {
      if (!value) { if (input.hasAttribute("data-nullable")) body[input.name] = null; continue; }
      value = Number(value);
      if (!Number.isFinite(value)) throw new Error("Введите число.");
    } else if (input.hasAttribute("data-lines")) {
      value = value.split("\n").map(line => line.trim()).filter(Boolean);
    } else if (input.hasAttribute("data-json")) {
      if (!value.trim()) continue;
      try { value = JSON.parse(value); } catch { throw new Error("Проверьте формат JSON в поле «" + input.closest("label").childNodes[0].textContent.trim() + "»."); }
    }
    if (input.name.includes(".")) {
      const [group, name] = input.name.split(".", 2);
      body[group] ||= {};
      body[group][name] = value;
    } else body[input.name] = value;
  }
  if (body.repeat_password !== undefined) { if (body.password !== body.repeat_password) throw new Error("Пароли не совпадают."); delete body.repeat_password; }
  if (body.variant_sku === "") body.variant_sku = null;
  if (form.hasAttribute("data-registration")) body.ref = new URLSearchParams(location.search).get("ref") || null;
  return body;
}

function formError(form, message) {
  const box = form.querySelector("[data-error]");
  if (box) { box.textContent = message; box.hidden = false; }
  else notice(message);
}

async function refresh() {
  if (!navigator.onLine || document.documentElement.dataset.offlineCopy) return;
  const page = document.querySelector("#page");
  if (!page) return;
  const path = currentPath();
  const headers = {};
  if (token()) headers.authorization = `Bearer ${token()}`;
  if (path === "/cart") headers["x-cart-session"] = cartSession();
  const endpoint = mirror ? "views/" + (path === "/" ? "home" : path.slice(1)) + ".html" : "views" + (path === "/" ? "/" : path) + location.search;
  try {
    const response = await fetch(url(endpoint), { headers, cache: "no-store" });
    if (response.status === 401) {
      store(path.startsWith("/admin") ? officeKey : memberKey, null);
      return refresh();
    }
    if (!response.ok) throw new Error("Не удалось обновить страницу. Попробуйте позже.");
    const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
    const next = parsed.querySelector("#page");
    if (!next) throw new Error("Не удалось обновить страницу. Попробуйте позже.");
    const contacts = path === "/cart" ? [...page.querySelectorAll("form[data-checkout] [name]")].map(input => ({ name: input.name, value: input.value, checked: input.checked })) : [];
    page.replaceWith(document.importNode(next, true));
    hideSkeleton();
    for (const saved of contacts) {
      const input = document.querySelector("form[data-checkout]")?.elements.namedItem(saved.name);
      if (input) { input.value = saved.value; if (input.type === "checkbox") input.checked = saved.checked; }
    }
    setupPage();
    void updateCartCount();
    return true;
  } catch (error) { notice(error.message); return false; }
}

async function refreshLiveView() {
  const live = document.documentElement.dataset.live;
  if (live === "cart" || (live && token(live === "office"))) await refresh();
}

async function updateCartCount() {
  if (!navigator.onLine || document.documentElement.dataset.offlineCopy) return;
  if (!stored("club_cart")) return;
  try {
    const cart = await request("/cart");
    document.querySelectorAll("[data-cart-count]").forEach(node => { node.textContent = cart.count || ""; });
  } catch {}
}

function checkoutReceipt(result) {
  const section = element("main", undefined, { id: "main", class: "site-article" });
  const heading = element("h1", "Заявка отправлена в учебный офис", { tabindex: "-1" });
  section.append(heading, element("p", "Номер заявки: " + result.number), element("p", "Учебный офис проверит состав и сумму заявки и свяжется с вами по указанным контактам."), element("p", "Предварительная сумма " + rubles(result.total_estimate)));
  if (!result.notified?.ok) section.append(element("p", notNotifiedText, { role: "alert" }));
  const payment = safeLink(result.payment_url, true);
  if (payment) section.append(element("a", "Оплатить через ЮKassa", { href: payment, class: "site-primary" }));
  section.append(element("a", token() ? "К моим заявкам" : "Войти в кабинет", { href: url("lk#orders") }));
  document.querySelector("#page").replaceChildren(section);
  heading.focus();
  window.scrollTo(0, 0);
}

function rubles(value) {
  return new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", minimumFractionDigits: value % 100 ? 2 : 0 }).format(value / 100);
}

function discountText(discount, source, message) {
  if (message) return message;
  return source === "member" ? "Скидка выпускника " + discount + "%." : "";
}

function applicationReceipt(form, result) {
  const receipt = element("div", undefined, { role: "status", class: "portfolio-receipt", tabindex: "-1" });
  receipt.append(element("h3", "Заявка " + result.number + " принята"), element("p", "Участников: " + result.participants + " · предварительная сумма " + rubles(result.total_estimate)));
  if (result.starts_on) receipt.append(element("p", "Старт потока: " + (form.querySelector("[name='cohort_id']:checked")?.dataset.start || result.starts_on)));
  const discount = discountText(result.member_discount, result.discount_source, result.message);
  if (discount) receipt.append(element("p", discount));
  receipt.append(element("p", "Учебный офис свяжется с вами по указанным контактам, чтобы согласовать договор и оплату."));
  if (!result.notified?.ok) receipt.append(element("p", notNotifiedText, { role: "alert" }));
  form.replaceWith(receipt);
  receipt.focus();
}

function jsonOrNull(value) {
  try { return JSON.parse(value); } catch { return null; }
}

async function sha256hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

async function fingerprint(value) {
  return sha256hex(JSON.stringify(value));
}

async function solveCaptcha() {
  const task = await request("/portfolio/challenge");
  for (let number = 0; number <= task.maxnumber; number++) {
    if (await sha256hex(task.salt + number) === task.challenge) return { salt: task.salt, number, signature: task.signature };
  }
  throw new Error("Не удалось отправить заявку. Обновите страницу и попробуйте еще раз.");
}

function showQuote(form, text, failed = false) {
  const box = form.querySelector("[data-quote]");
  box.textContent = text;
  box.dataset.state = failed ? "error" : "ok";
}

async function quotePromo(button) {
  const form = button.form;
  const code = form.elements.promo_code.value.trim();
  if (!code) { showQuote(form, "Введите промокод.", true); return; }
  const applicant = form.querySelector("[data-applicant]:checked").value;
  const body = { applicant, promo_code: code, cohort_id: form.querySelector("[name='cohort_id']:checked")?.value };
  if (applicant === "organization") body.participants = Number(form.elements.participants.value) || undefined;
  button.disabled = true;
  try {
    const quote = await request(button.dataset.quoteApi, "POST", body);
    showQuote(form, [discountText(quote.discount, quote.source, quote.message), "Предварительная сумма " + rubles(quote.total) + "."].filter(Boolean).join(" "));
  } catch (error) { showQuote(form, error.message, true); }
  finally { button.disabled = false; }
}

function toggleApplicant(input) {
  const group = input.form.querySelector("[data-organization]");
  const organization = input.value === "organization";
  group.hidden = !organization;
  group.querySelectorAll("input").forEach(field => { field.disabled = !organization; });
}

async function perform(target, body) {
  if (target.hasAttribute("data-member-action") && !token(false)) {
    location.href = url("lk?next=" + encodeURIComponent(currentPath() + location.hash));
    return;
  }
  if (target.dataset.confirm && !window.confirm(target.dataset.confirm)) return;
  const path = target.dataset.api;
  const method = target.dataset.method || "POST";
  const cart = target.hasAttribute("data-cart");
  const checkout = target.hasAttribute("data-checkout");
  const application = target.hasAttribute("data-application");
  if (checkout && pendingCart) throw new Error("Дождитесь обновления корзины.");
  if (cart && pendingCheckout) throw new Error("Дождитесь отправки заявки.");
  const buttons = target.tagName === "FORM" ? [...target.querySelectorAll("button[type='submit'],button:not([type])")] : [target];
  if (buttons.some(button => button.disabled)) return;
  buttons.forEach(button => { button.disabled = true; });
  const oldText = buttons.map(button => button.textContent);
  buttons.forEach(button => { button.textContent = "Подождите…"; });
  const fields = checkout || application ? [...target.querySelectorAll("input,select,textarea")].map(input => ({ input, disabled: input.disabled })) : [];
  fields.forEach(({ input }) => { input.disabled = true; });
  let headers = {};
  try {
    if (cart) pendingCart++;
    if (checkout) {
      pendingCheckout = true;
      const key = "club_checkout:" + cartSession();
      const id = stored(key) || crypto.randomUUID();
      store(key, id);
      headers = { "idempotency-key": id };
    }
    if (application) {
      const key = "club_application:" + path;
      const print = await fingerprint([body, token() ? "member" : "guest"]);
      const saved = jsonOrNull(stored(key));
      const id = saved?.print === print ? saved.id : crypto.randomUUID();
      store(key, JSON.stringify({ id, print }));
      headers = { "idempotency-key": id };
      body = { ...body, captcha: await solveCaptcha() };
    }
    if (target.hasAttribute("data-support-create")) {
      const saved = sessionStorage.getItem("club-support-pending");
      const pending = saved ? JSON.parse(saved) : { id: crypto.randomUUID(), key: [...crypto.getRandomValues(new Uint8Array(32))].map(value => value.toString(16).padStart(2, "0")).join("") };
      sessionStorage.setItem("club-support-pending", JSON.stringify(pending));
      body = { ...body, ...pending };
    }
    if (body && path.startsWith("/admin/support/") && !body.message?.trim()) delete body.message;
    const result = await request(path, method, body, headers);
    if (target.dataset.login) {
      if (!result.token) throw new Error("Не удалось подтвердить вход.");
      if (!store(target.dataset.login === "office" ? officeKey : memberKey, result.token)) throw new Error("Не удалось сохранить вход. Разрешите хранилище браузера.");
      const next = new URLSearchParams(location.search).get("next");
      if (next?.startsWith("/") && !next.startsWith("//") && !/[\\\x00-\x20]/.test(next)) location.href = url(next);
      else await refresh();
    } else if (application) {
      store("club_application:" + path, null);
      applicationReceipt(target, result);
    } else if (checkout) {
      store("club_checkout:" + cartSession(), null);
      checkoutReceipt(result);
      void updateCartCount();
    } else if (target.hasAttribute("data-registration")) {
      target.replaceWith(element("div", result.confirm_required ? "Проверьте почту: подтвердите адрес по ссылке из письма. Затем заявку проверит учебный офис." : "Заявка сохранена. Учебный офис проверит выпуск. Обычно это занимает 1–2 рабочих дня.", { role: "status", class: "site-panel" }));
    } else if (target.hasAttribute("data-delete-profile")) {
      store(memberKey, null);
      location.href = url("/");
    } else if (target.hasAttribute("data-support-create")) {
      sessionStorage.setItem("club-support-access", JSON.stringify({ id: result.id, key: body.key }));
      sessionStorage.removeItem("club-support-pending");
      const receipt = element("div", undefined, { role: "status", class: "site-panel" });
      receipt.append(element("p", "Обращение отправлено. Сохраните номер и код доступа."), element("p", "Номер: " + result.id), element("p", "Код: " + body.key));
      target.replaceWith(receipt);
      await supportThread({ id: result.id, key: body.key });
    } else if (result.payment_url) {
      const payment = safeLink(result.payment_url, true);
      if (!payment) throw new Error("Не удалось получить ссылку на оплату. Обратитесь в поддержку.");
      const link = element("a", "Оплатить через ЮKassa", { href: payment, class: "site-primary" });
      target.after(link);
      notice("Заявка " + result.number + " сохранена.");
    } else if (path === "/me/tg-link" && result.url) {
      const link = safeLink(result.url);
      if (link && new URL(link).hostname === "t.me") target.after(element("a", "Открыть Telegram", { href: link, target: "_blank", rel: "noopener noreferrer" }));
    } else if (target.dataset.success) {
      notice(target.dataset.success);
      target.reset();
    } else if (path === "/podcasts/subscribe") {
      notice("Заявка на подписку сохранена: " + result.number + ". Учебный офис свяжется с вами.");
    } else {
      if (cart) store("club_checkout:" + cartSession(), null);
      if (cart && currentPath() !== "/cart") {
        void updateCartCount();
        notice("Корзина обновлена.");
      } else if (await refresh()) notice(cart ? "Корзина обновлена." : "Изменения сохранены");
    }
  } finally {
    if (checkout) pendingCheckout = false;
    fields.forEach(({ input, disabled }) => { input.disabled = disabled; });
    if (cart) pendingCart--;
    buttons.forEach((button, index) => { button.disabled = false; button.textContent = oldText[index]; });
  }
}

function reading(strict = false) {
  try {
    const parsed = JSON.parse(stored(readingKey, '{"saved":[],"recent":[],"read":[]}'));
    const prefixes = { change: "changes", program: "dpo", portfolio: "portfolio", podcast: "podcasts", event: "events", news: "news", merch: "merch" };
    const clean = values => [...new Map((Array.isArray(values) ? values : []).filter(item => item && Object.hasOwn(prefixes, item.kind) && /^[\w-]{1,200}$/.test(item.id) && typeof item.title === "string" && item.title.trim() && item.title.length <= 1000 && item.path === "/" + prefixes[item.kind] + "/" + item.id && Number.isFinite(item.at) && item.at > 0).map(item => [item.path, item])).values()];
    return { saved: clean(parsed.saved).slice(0, 200), recent: clean(parsed.recent).slice(0, 12), read: Array.isArray(parsed.read) ? parsed.read.filter(value => /^\/changes\/[\w-]{1,200}$/.test(value)).slice(0, 500) : [] };
  } catch { if (strict) throw new Error("Сохранённые материалы повреждены. Выгрузите данные браузера перед очисткой хранилища."); return { saved: [], recent: [], read: [] }; }
}

function writeReading(data) {
  store(readingKey, JSON.stringify(data));
  renderReading();
}

function renderReading() {
  const data = reading();
  document.querySelectorAll("[data-action='save']").forEach(button => {
    const on = data.saved.some(item => item.path === button.dataset.href);
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "Сохранено" : "Сохранить";
  });
  document.querySelectorAll("[data-action='mark-read']").forEach(button => {
    const on = data.read.includes(button.dataset.href);
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "Прочитано" : "Отметить прочитанным";
  });
  document.querySelectorAll("[data-reading-list]").forEach(list => {
    list.replaceChildren();
    const kind = document.querySelector("[data-reading-kind]")?.value;
    const query = (document.querySelector("[data-reading-search]")?.value || "").toLocaleLowerCase("ru").replace(/ё/g, "е").trim();
    const items = list.dataset.readingList === "recent" ? data.recent.slice(0, 4) : data.saved.filter(item => (!kind || item.kind === kind) && item.title.toLocaleLowerCase("ru").replace(/ё/g, "е").includes(query));
    if (!items.length) list.append(element("p", list.dataset.readingList === "saved" && data.saved.length ? "По выбранным условиям ничего не найдено." : "Материалов пока нет.", { role: "status" }));
    for (const item of items) {
      const row = element("div", undefined, { class: "site-actions" });
      row.append(element("a", item.title, { href: url(item.path), class: "site-reading-item" }));
      if (list.dataset.readingList === "saved") row.append(element("button", "Удалить", { "data-action": "remove-saved", "data-href": item.path, "aria-label": "Удалить «" + item.title + "»" }));
      list.append(row);
    }
  });
  if (mirror) document.dispatchEvent(new CustomEvent("club-reading-updated", { detail: { read: data.read } }));
  else document.querySelectorAll("[data-reading-path]").forEach(article => { article.hidden = !!document.querySelector("[data-unread-filter]")?.checked && data.read.includes(article.dataset.readingPath); });
}

function alignOfficeNavigation() {
  const navigation = document.querySelector("#office-navigation");
  const active = navigation?.querySelector("[aria-current]");
  if (!active || !navigation.clientHeight) return;
  const container = navigation.getBoundingClientRect();
  const item = active.getBoundingClientRect();
  if (item.bottom > container.bottom) navigation.scrollTop += item.bottom - container.bottom + 8;
  if (item.top < container.top) navigation.scrollTop += item.top - container.top - 8;
}

let cabinetSectionOpened = false;

function openCabinetSection() {
  if (cabinetSectionOpened || currentPath() !== "/lk") return;
  const section = location.hash.slice(1) || new URLSearchParams(location.search).get("section");
  if (!["club-overview", "orders", "community", "achievements"].includes(section)) return;
  const target = document.getElementById(section);
  if (!target) return;
  target.scrollIntoView({ block: "start", behavior: "instant" });
  cabinetSectionOpened = true;
}

function setupPage() {
  prepareDiscovery(url, mirror);
  prepareCatalog(url, notice);
  prepareHome();
  alignOfficeNavigation();
  openCabinetSection();
  const recent = document.querySelector("[data-reading-title]");
  if (recent) {
    try {
      const data = reading(true);
      const path = currentPath();
      data.recent = [{ kind: ({ dpo: "program", podcasts: "podcast", events: "event", changes: "change" })[recent.dataset.readingKind] || recent.dataset.readingKind, id: path.split("/").pop(), title: recent.dataset.readingTitle, path, at: Date.now() }, ...data.recent.filter(item => item.path !== path)].slice(0, 12);
      store(readingKey, JSON.stringify(data));
    } catch (error) { notice(error.message); }
  }
  renderReading();
  document.querySelectorAll("[data-member-link]").forEach(link => {
    link.dataset.guestLabel ||= link.textContent;
    link.dataset.guestHref ||= link.getAttribute("href");
    link.href = token(false) ? url("lk") : link.dataset.guestHref;
    link.textContent = token(false) ? "Кабинет" : link.dataset.guestLabel;
  });
  document.querySelectorAll("[data-reveal]").forEach(node => { node.setAttribute("data-revealed", "true"); node.classList.add("is-visible", "is-in"); });
  document.querySelectorAll("audio").forEach(audio => {
    const id = audio.closest("[data-episode]")?.dataset.episode;
    if (!id) return;
    const key = "pod-pos-" + id;
    audio.addEventListener("loadedmetadata", () => { const position = Number(stored(key, "0")); if (Number.isFinite(position) && position < audio.duration - 5) audio.currentTime = Math.max(0, position); });
    audio.addEventListener("timeupdate", () => store(key, String(Math.floor(audio.currentTime))));
    let retried = false;
    let refreshing = false;
    const player = audio.closest(".site-player");
    const retry = player.querySelector("[data-audio-retry]");
    const failure = player.querySelector("[data-audio-error]");
    const renew = async () => {
      if (refreshing || mirror) return;
      refreshing = true;
      const position = audio.currentTime;
      const playing = !audio.paused;
      try {
        const fresh = await request("/podcasts");
        const source = safeLink(fresh.items.find(item => item.id === id)?.audio_url);
        if (!source) throw new Error("Ссылка на аудио недоступна. Проверьте подписку или повторите позже.");
        failure.hidden = true;
        retry.hidden = true;
        audio.addEventListener("loadedmetadata", () => {
          if (position > 0 && position < audio.duration - 5) audio.currentTime = position;
          if (playing) void audio.play().catch(() => {});
        }, { once: true });
        audio.src = source;
        audio.load();
      } catch (error) {
        failure.textContent = error.message;
        failure.hidden = false;
        retry.hidden = false;
      } finally { refreshing = false; }
    };
    retry.addEventListener("click", () => { retried = true; void renew(); });
    audio.addEventListener("error", () => {
      if (!retried && !mirror) { retried = true; void renew(); }
      else { failure.textContent = "Не удалось загрузить аудио. Проверьте соединение и повторите."; failure.hidden = false; retry.hidden = mirror; }
    });
  });
  if (mirror) document.querySelectorAll("[data-api] button,button[data-api],input[type='file']").forEach(button => { if (!button.closest("[data-cart]")) { button.disabled = true; button.title = "В демоверсии отправка и изменение данных отключены."; } });
  document.dispatchEvent(new Event("club-page-ready"));
}

async function supportThread(access) {
  if (!/^[a-f0-9]{64}$/.test(access.key) || !/^[0-9a-f-]{36}$/.test(access.id)) throw new Error("Проверьте номер обращения и код доступа.");
  const thread = await request("/support/" + access.id, "GET", undefined, { "x-support-key": access.key });
  const section = document.querySelector("#support-thread");
  section.replaceChildren(element("h3", "Переписка по обращению " + access.id));
  for (const message of thread.messages || []) section.append(element("p", (message.author === "visitor" ? "Вы: " : "Поддержка: ") + message.text));
  if (thread.status !== "closed") {
    const form = element("form", undefined, { "data-support-message": "true" });
    form.append(element("label", "Сообщение"));
    const textarea = element("textarea", undefined, { name: "message", required: "true", minlength: "5", maxlength: "4000", "aria-label": "Сообщение в поддержку" });
    form.append(textarea, element("button", "Отправить"));
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const button = form.querySelector("button");
      button.disabled = true;
      try { await request("/support/" + access.id + "/messages", "POST", { message: textarea.value }, { "x-support-key": access.key }); await supportThread(access); }
      catch (error) { notice(error.message); } finally { button.disabled = false; }
    });
    section.append(form);
  }
  const remove = element("button", "Удалить обращение", { class: "site-danger" });
  remove.addEventListener("click", async () => {
    if (!window.confirm("Удалить переписку по этому обращению?")) return;
    try { await request("/support/" + access.id, "DELETE", undefined, { "x-support-key": access.key }); section.replaceChildren(element("p", "Обращение удалено.")); sessionStorage.removeItem("club-support-access"); }
    catch (error) { notice(error.message); }
  });
  section.append(remove);
}

async function download(button) {
  if (mirror) throw new Error("Выгрузки доступны в рабочем приложении.");
  const headers = token() ? { authorization: `Bearer ${token()}` } : {};
  const response = await fetch(url("api" + button.dataset.download), { headers });
  if (!response.ok) throw new Error("Не удалось скачать файл. Проверьте доступ и повторите.");
  const blobUrl = URL.createObjectURL(await response.blob());
  const link = element("a", undefined, { href: blobUrl, download: button.dataset.filename || "club-export" });
  link.click();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
}

document.addEventListener("submit", async event => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || (!form.dataset.api && !form.hasAttribute("data-support-open"))) return;
  event.preventDefault();
  const errorBox = form.querySelector("[data-error]");
  if (errorBox) errorBox.hidden = true;
  try {
    if (form.hasAttribute("data-support-open")) { await supportThread(formBody(form)); return; }
    await perform(form, form.hasAttribute("data-file") ? new FormData(form) : formBody(form));
  } catch (error) { formError(form, error.message); }
});

document.addEventListener("click", async event => {
  const target = event.target.closest("[data-action],button[data-api],[data-download]");
  if (!target) return;
  try {
    if (target.dataset.download) { await download(target); return; }
    if (target.dataset.api) { await perform(target, target.dataset.method === "GET" || target.dataset.method === "DELETE" ? undefined : target.dataset.body ? JSON.parse(target.dataset.body) : {}); return; }
    const action = target.dataset.action;
    if (action === "refresh") await refresh();
    else if (action === "quote") await quotePromo(target);
    else if (action === "office-menu") {
      const open = target.getAttribute("aria-expanded") !== "true";
      target.setAttribute("aria-expanded", String(open));
      document.querySelector("#office-navigation").dataset.open = String(open);
      if (open) alignOfficeNavigation();
    } else if (action === "menu") {
      const on = target.getAttribute("aria-expanded") !== "true";
      target.setAttribute("aria-expanded", String(on));
      target.setAttribute("aria-label", on ? "Закрыть меню" : "Открыть меню");
      document.querySelector("#club-menu").dataset.open = String(on);
    } else if (action === "logout" || action === "logout-office") {
      let failure;
      try { if (action === "logout-office" && token(true)) await request("/auth/admin-logout", "POST", {}); }
      catch (error) { failure = error; }
      finally { store(action === "logout-office" ? officeKey : memberKey, null); }
      if (action === "logout") store("club_cart", null);
      await refresh();
      if (failure) throw failure;
    } else if (action === "cookies") document.querySelector("#cookie-banner").hidden = false;
    else if (action.startsWith("cookies-")) {
      store("club_cookie_consent", action === "cookies-all" ? "all" : "essential");
      document.querySelector("#cookie-banner").hidden = true;
      if (action === "cookies-all") void pageview();
    } else if (action.startsWith("vision")) vision(action);
    else if (action === "save") {
      const data = reading(true);
      const path = target.dataset.href;
      if (data.saved.some(item => item.path === path)) data.saved = data.saved.filter(item => item.path !== path);
      else {
        if (data.saved.length >= 200) throw new Error("Уже сохранено 200 материалов. Удалите один, чтобы добавить новый.");
        const kind = { dpo: "program", podcasts: "podcast", events: "event", changes: "change" }[target.dataset.kind] || target.dataset.kind;
        data.saved.unshift({ path, title: target.dataset.title, kind, id: path.split("/").pop(), at: Date.now() });
      }
      writeReading(data);
    } else if (action === "remove-saved") {
      const data = reading(true);
      const removed = data.saved.find(item => item.path === target.dataset.href);
      if (!removed) { renderReading(); return; }
      data.saved = data.saved.filter(item => item.path !== target.dataset.href);
      if (!store(readingKey, JSON.stringify(data))) return;
      removedReading = removed;
      renderReading();
      const box = document.querySelector("[data-reading-undo]");
      if (box) { box.hidden = false; box.querySelector("span").textContent = "Удалено: «" + removed.title + "»."; box.querySelector("button").focus(); }
    } else if (action === "undo-reading") {
      const data = reading(true);
      if (!removedReading) return;
      if (data.saved.length >= 200 && !data.saved.some(item => item.path === removedReading.path)) throw new Error("Уже сохранено 200 материалов. Освободите место, чтобы отменить удаление.");
      if (!data.saved.some(item => item.path === removedReading.path)) data.saved.unshift(removedReading);
      if (!store(readingKey, JSON.stringify(data))) return;
      removedReading = null;
      renderReading();
      document.querySelector("[data-reading-search]")?.focus();
      document.querySelector("[data-reading-undo]").hidden = true;
    }
    else if (action === "mark-read") {
      const data = reading(true);
      const path = target.dataset.href;
      data.read = data.read.includes(path) ? data.read.filter(value => value !== path) : [path, ...data.read].slice(0, 500);
      writeReading(data);
    } else if (action === "clear-reading") {
      const data = reading(true); data.recent = []; writeReading(data);
    } else if (action === "copy" || action === "copy-invite") {
      await copyText(action === "copy" ? target.dataset.value : location.origin + url("join?ref=" + encodeURIComponent(target.dataset.code)), target);
    } else if (action === "media-preview") {
      const response = await fetch(url("api/admin/media/" + target.dataset.fileId + "/content"), { headers: { authorization: `Bearer ${token(true)}` } });
      if (!response.ok) throw new Error("Не удалось открыть файл. Проверьте доступ и повторите.");
      const blob = URL.createObjectURL(await response.blob());
      const content = document.querySelector("#dialog-content");
      content.replaceChildren(element("h2", target.dataset.title));
      const type = target.dataset.fileType.startsWith("image/") ? "img" : "audio";
      content.append(element(type, undefined, { src: blob, ...(type === "img" ? { alt: target.dataset.title } : { controls: "" }) }));
      const dialog = document.querySelector("#site-dialog");
      dialog.addEventListener("close", () => { URL.revokeObjectURL(blob); content.replaceChildren(); }, { once: true });
      dialog.showModal();
    } else if (action === "close-dialog") document.querySelector("#site-dialog").close();
    else if (action.startsWith("audio-")) {
      const audio = target.closest(".site-player").querySelector("audio");
      audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + (action === "audio-back" ? -15 : 15)));
    } else if (action === "video") {
      const href = safeLink(target.dataset.video);
      if (!href || new URL(href).hostname !== "rutube.ru") throw new Error("Видео недоступно.");
      target.replaceWith(element("iframe", undefined, { src: href, title: "Видео подкаста", allow: "fullscreen", allowfullscreen: "", loading: "lazy", class: "site-video" }));
    } else if (action === "leave-mini") {
      sessionStorage.removeItem("club_telegram_preview"); sessionStorage.removeItem("club_pwa"); location.href = url("");
    } else if (action === "install" && installPrompt) {
      await installPrompt.prompt(); installPrompt = null; target.hidden = true;
    } else if (action === "telegram-login") {
      const initData = window.Telegram?.WebApp?.initData;
      if (!initData) throw new Error("Откройте мини-приложение клуба в Telegram или войдите по почте.");
      const response = await request("/auth/telegram", "POST", { initData });
      if (!response.token || !store(memberKey, response.token)) throw new Error("Не удалось сохранить вход. Разрешите хранилище браузера.");
      location.href = url("lk");
    } else if (action === "push-subscribe") await subscribePush();
  } catch (error) { notice(error.message); }
});

function vision(action) {
  const html = document.documentElement;
  if (action === "vision") { html.classList.toggle("vis"); document.querySelector("#vision").hidden = !html.classList.contains("vis"); }
  else if (action === "vision-size") { const current = Number(html.style.getPropertyValue("--vis-zoom") || "1"); html.style.setProperty("--vis-zoom", String(current >= 1.4 ? 1 : Math.round((current + 0.2) * 10) / 10)); }
  else if (action === "vision-scheme") { const schemes = ["bw", "wb", "bb"]; html.dataset.visScheme = schemes[(schemes.indexOf(html.dataset.visScheme || "bw") + 1) % 3]; }
  else if (action === "vision-images") html.classList.toggle("vis-noimg");
  else if (action === "vision-serif") html.classList.toggle("vis-serif");
  else if (action === "vision-spacing") html.classList.toggle("vis-spacing");
  store("club_vision", JSON.stringify({ on: html.classList.contains("vis"), zoom: html.style.getPropertyValue("--vis-zoom") || "1", scheme: html.dataset.visScheme || "bw", images: !html.classList.contains("vis-noimg"), serif: html.classList.contains("vis-serif"), spacing: html.classList.contains("vis-spacing") }));
  document.dispatchEvent(new Event("club-vision-change"));
}

async function pageview() {
  if (mirror || !["all", "1"].includes(stored("club_cookie_consent")) || currentPath().startsWith("/admin")) return;
  try { await fetch(url("api/analytics/pageview"), { method: "POST", credentials: "omit", headers: { "content-type": "application/json" }, body: JSON.stringify({ path: currentPath() }) }); } catch {}
}

async function subscribePush() {
  if (!token(false)) throw new Error("Сначала войдите в кабинет.");
  if (!navigator.serviceWorker || !window.PushManager || !window.isSecureContext) throw new Error("Уведомления недоступны в этом браузере.");
  const config = await request("/push/vapid");
  if (!config.key) throw new Error("Уведомления пока не подключены.");
  const registration = await navigator.serviceWorker.ready;
  const padded = config.key.replace(/-/g, "+").replace(/_/g, "/");
  const key = Uint8Array.from(atob(padded), character => character.charCodeAt(0));
  const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  await request("/me/push/subscribe", "POST", subscription.toJSON()); notice("Уведомления включены");
}

document.addEventListener("input", event => {
  if (event.target.matches("[data-reading-search]")) renderReading();
  if (event.target.matches("[data-quote-input]")) showQuote(event.target.form, "");
});
document.addEventListener("focusin", event => {
  if (event.target.matches?.(":focus-visible")) event.target.closest(".dpo-starts-grid article, .cabinet-nav a")?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
});
document.addEventListener("change", event => {
  if (event.target.matches("[data-audio-rate]")) event.target.closest(".site-player").querySelector("audio").playbackRate = Number(event.target.value);
  if (event.target.matches("[data-unread-filter],[data-reading-kind]")) renderReading();
  if (event.target.matches("[data-applicant]")) { toggleApplicant(event.target); showQuote(event.target.form, ""); }
  if (event.target.matches("input[name='interests']")) {
    const selected = event.target.form.querySelectorAll("input[name='interests']:checked");
    if (selected.length > Number(event.target.closest("fieldset").dataset.maxInterests)) { event.target.checked = false; notice("Выбрано максимальное число интересов."); }
  }
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    const button = document.querySelector("[data-action='office-menu']");
    if (button?.getAttribute("aria-expanded") === "true") { button.click(); button.focus(); }
  }
  if (event.key === "Escape") { const button = document.querySelector("[data-action='menu']"); if (button?.getAttribute("aria-expanded") === "true") button.click(); }
});
document.addEventListener("club-reading-refresh", renderReading);
window.addEventListener("storage", event => { if (event.key === readingKey) renderReading(); });
window.addEventListener("beforeinstallprompt", event => { event.preventDefault(); installPrompt = event; document.querySelector("#install-app").hidden = false; });

try {
  const settings = JSON.parse(stored("club_vision", "{}"));
  const html = document.documentElement;
  html.classList.toggle("vis", !!settings.on); html.dataset.visScheme = settings.scheme || "bw";
  html.style.setProperty("--vis-zoom", String([1, 1.2, 1.4].includes(Number(settings.zoom)) ? settings.zoom : 1));
  for (const [key, css] of [["images", "vis-noimg"], ["serif", "vis-serif"], ["spacing", "vis-spacing"]]) html.classList.toggle(css, key === "images" ? settings.images === false : !!settings[key]);
  document.querySelector("#vision").hidden = !settings.on;
} catch {}
document.querySelector("#cookie-banner").hidden = ["all", "essential", "0", "1"].includes(stored("club_cookie_consent"));
prepareMini(currentPath(), url);
const chromeSizes = [[".mobile-tabs", "--tabs-h", 0], ["#cookie-banner", "--cookie-h", 32], ["#install-app", "--install-h", 32]];
const measureChrome = () => {
  for (const [selector, property, gap] of chromeSizes) {
    const node = document.querySelector(selector);
    document.documentElement.style.setProperty(property, (node?.getClientRects().length ? node.getBoundingClientRect().height + gap : 0) + "px");
  }
};
const chromeObserver = new ResizeObserver(measureChrome);
for (const [selector] of chromeSizes) { const node = document.querySelector(selector); if (node) chromeObserver.observe(node); }
window.addEventListener("resize", measureChrome);
measureChrome();
const mirrorNotice = document.querySelector(".site-notice");
if (mirrorNotice) new ResizeObserver(() => {
  document.body.style.setProperty("--office-notice-height", mirrorNotice.offsetHeight + "px");
  alignOfficeNavigation();
}).observe(mirrorNotice);
function offlineNotice() {
  const box = document.querySelector("#offline-notice");
  if (!box) return;
  const timestamp = Number(document.documentElement.dataset.offlineCopy);
  box.hidden = navigator.onLine && !timestamp;
  const message = box.querySelector("[data-offline-message]");
  message.textContent = timestamp
    ? "Сохраненная копия от " + new Date(timestamp).toLocaleString("ru-RU") + ". Для актуальных данных подключитесь к интернету и обновите страницу."
    : "Нет подключения к интернету. Открытые раньше страницы можно читать, вход и отправка заявок недоступны.";
  box.querySelector("button").hidden = !navigator.onLine;
}

function saveReadingPage() {
  if (readingPageSaved || !navigator.onLine || document.documentElement.dataset.offlineCopy || document.body.dataset.offlineReading !== "true" || !navigator.serviceWorker.controller) return;
  readingPageSaved = true;
  void fetch(location.href, { credentials: "omit", headers: { "x-club-save-page": "1" } }).catch(() => { readingPageSaved = false; });
}

document.querySelector("[data-action='reload-online']")?.addEventListener("click", () => location.reload());
window.addEventListener("offline", offlineNotice);
window.addEventListener("online", () => { offlineNotice(); saveReadingPage(); });
offlineNotice();
if (navigator.serviceWorker && window.isSecureContext) {
  navigator.serviceWorker.addEventListener("controllerchange", saveReadingPage);
  void navigator.serviceWorker.register(url("sw.js"), { scope: base }).then(() => navigator.serviceWorker.ready).then(saveReadingPage).catch(() => {});
}
setupPage();
prepareSkeleton(base);
void refreshLiveView().finally(hideSkeleton);
void updateCartCount();
void pageview();

