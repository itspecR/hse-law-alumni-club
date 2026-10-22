export function miniStartRoute(value) {
  const sections = { club: "tg", dpo: "dpo", events: "events", news: "news", podcasts: "podcasts", merch: "merch", lk: "lk", support: "support" };
  if (Object.hasOwn(sections, value || "")) return sections[value];
  return /^p_[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value || "") ? "dpo/" + value.slice(2) : null;
}

function hexColor(value) {
  const channels = (value.match(/\d+/g) || []).slice(0, 3);
  return "#" + channels.map(channel => Number(channel).toString(16).padStart(2, "0")).join("");
}

function surfaceColor(selector) {
  return hexColor(getComputedStyle(document.querySelector(selector) || document.body).backgroundColor);
}

export function prepareMini(path, url) {
  if (path === "/admin" || path.startsWith("/admin/")) return;
  const query = new URLSearchParams(location.search);
  const entry = path === "/tg";
  let preview = false;
  try {
    if (query.get("site") === "1") {
      sessionStorage.removeItem("club_telegram_preview");
      sessionStorage.removeItem("club_pwa");
      if (!window.Telegram?.WebApp?.initData) return;
    }
    if (query.get("pwa") === "1" || query.get("source") === "pwa" || matchMedia("(display-mode: standalone)").matches) sessionStorage.setItem("club_pwa", "1");
    if (sessionStorage.getItem("club_pwa") === "1") document.documentElement.classList.add("pwa-shell");
    if (entry || query.has("tgWebAppData") || new URLSearchParams(location.hash.slice(1)).has("tgWebAppData")) sessionStorage.setItem("club_telegram_preview", "1");
    preview = sessionStorage.getItem("club_telegram_preview") === "1";
  } catch {}
  if (document.documentElement.classList.contains("pwa-shell")) {
    document.querySelectorAll("[data-mini-preview]").forEach(node => { node.textContent = "Мобильный просмотр"; });
    document.querySelectorAll("[data-mobile-preview]").forEach(node => {
      node.textContent = "Обычный сайт";
      node.href = url("?site=1");
    });
  }
  if (!preview && !window.Telegram?.WebApp?.initData) return;
  document.documentElement.classList.add("telegram-mini");
  const club = document.querySelector(".mobile-tabs a");
  if (club) club.href = url("tg");
  const start = miniStartRoute(query.get("startapp"));
  if (entry && start && start !== "tg") { location.replace(url(start)); return; }
  function connected() {
    const app = window.Telegram?.WebApp;
    if (!app?.initData) return;
    const page = surfaceColor("body");
    app.ready?.(); app.expand?.(); app.setHeaderColor?.(page); app.setBackgroundColor?.(page);
    if (!app.isVersionAtLeast || app.isVersionAtLeast("7.10")) app.setBottomBarColor?.(surfaceColor(".mobile-tabs"));
    const applyInsets = () => {
      for (const [name, edge] of [["--mini-top", "top"], ["--mini-bottom", "bottom"]]) {
        const size = (Number(app.safeAreaInset?.[edge]) || 0) + (Number(app.contentSafeAreaInset?.[edge]) || 0);
        document.documentElement.style.setProperty(name, Math.min(200, Math.max(0, size)) + "px");
      }
    };
    applyInsets();
    app.onEvent?.("safeAreaChanged", applyInsets); app.onEvent?.("contentSafeAreaChanged", applyInsets);
    document.querySelectorAll("[data-mini-preview]").forEach(node => { node.hidden = true; });
    document.querySelectorAll("[data-action='telegram-login']").forEach(node => { node.hidden = false; });
    if (entry) app.BackButton?.hide?.(); else app.BackButton?.show?.();
    const back = () => {
      let previous;
      try { previous = new URL(document.referrer); } catch {}
      const root = new URL(url(""), location.origin);
      if (history.length > 1 && previous?.origin === root.origin && previous.pathname.startsWith(root.pathname)) history.back();
      else location.href = url("tg");
    };
    app.BackButton?.onClick?.(back);
    window.addEventListener("pagehide", () => { app.BackButton?.offClick?.(back); app.offEvent?.("safeAreaChanged", applyInsets); app.offEvent?.("contentSafeAreaChanged", applyInsets); }, { once: true });
    const sdkStart = miniStartRoute(app.initDataUnsafe?.start_param);
    if (entry && sdkStart && sdkStart !== "tg" && !query.has("startapp")) location.replace(url(sdkStart));
  }
  if (window.Telegram?.WebApp) connected();
  else if (!document.documentElement.classList.contains("pwa-shell") || query.has("tgWebAppData") || new URLSearchParams(location.hash.slice(1)).has("tgWebAppData")) {
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-web-app.js";
    script.addEventListener("load", connected); document.head.append(script);
  }
}
