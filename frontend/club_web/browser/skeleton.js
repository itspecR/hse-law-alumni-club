const SKELETON_DELAY = 300;
const SKELETON_TIMEOUT = 10000;
const SKIPPED_PATHS = /^\/(?:api|assets|fonts)(?:\/|$)|\.ics$/;
const PANEL_PATHS = /^\/(?:lk|admin|cart)(?:\/|$)/;
let timer = 0;

export function skeletonKind(path) {
  if (PANEL_PATHS.test(path)) return "panel";
  return path.split("/").filter(Boolean).length > 1 ? "article" : "list";
}

function plainClick(event) {
  return !event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

function linkPath(event, root) {
  const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
  if (!link || link.hasAttribute("download") || link.target) return null;
  const target = new URL(link.href, location.href);
  if (target.origin !== location.origin || !target.pathname.startsWith(root)) return null;
  if (target.pathname === location.pathname && target.search === location.search) return null;
  const path = "/" + target.pathname.slice(root.length);
  return SKIPPED_PATHS.test(path) ? null : path;
}

function showSkeleton(kind) {
  document.documentElement.dataset.skeleton = kind;
  timer = setTimeout(hideSkeleton, SKELETON_TIMEOUT);
}

export function hideSkeleton() {
  clearTimeout(timer);
  delete document.documentElement.dataset.skeleton;
}

export function prepareSkeleton(base) {
  const root = new URL(base, location.href).pathname;
  window.addEventListener("click", event => {
    const path = plainClick(event) ? linkPath(event, root) : null;
    if (!path) return;
    clearTimeout(timer);
    timer = setTimeout(() => showSkeleton(skeletonKind(path)), SKELETON_DELAY);
  });
  window.addEventListener("pageshow", event => { if (event.persisted) hideSkeleton(); });
}
