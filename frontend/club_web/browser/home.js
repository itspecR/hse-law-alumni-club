import { hidePastStarts } from "./catalog.js";

const FAR_FROM = 0.42;
const REVEAL_MARGIN = "0px 0px -8% 0px";
const FOCUS_GAP = 8;
const reduce = matchMedia("(prefers-reduced-motion: reduce)");
let hero = null;
let queued = false;
let revealer = null;
let watcher = null;

function heroProgress() {
  const travel = hero.offsetHeight - hero.querySelector(".lux-hero__stage").offsetHeight;
  if (travel <= 0) return 0;
  return Math.min(1, Math.max(0, -hero.getBoundingClientRect().top / travel));
}

function paintHero() {
  queued = false;
  if (!hero?.isConnected) return;
  const progress = reduce.matches ? 0 : heroProgress();
  hero.style.setProperty("--p", progress.toFixed(4));
  hero.classList.toggle("is-far", progress > FAR_FROM);
}

function requestHero() {
  if (queued || !hero) return;
  queued = true;
  requestAnimationFrame(paintHero);
}

addEventListener("scroll", requestHero, { passive: true });
addEventListener("resize", requestHero);
reduce.addEventListener("change", requestHero);
document.addEventListener("focusin", event => event.target.closest(".lux-reveal")?.classList.add("is-in"));

function prepareReveal(root) {
  revealer?.disconnect();
  const nodes = [...root.querySelectorAll(".lux-reveal")];
  if (!("IntersectionObserver" in window)) { nodes.forEach(node => node.classList.add("is-in")); return; }
  revealer = new IntersectionObserver(entries => entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    entry.target.classList.add("is-in");
    revealer.unobserve(entry.target);
  }), { rootMargin: REVEAL_MARGIN });
  nodes.forEach(node => revealer.observe(node));
}

function swap(node, text) {
  if (node.textContent === text) return;
  node.textContent = text;
  node.classList.remove("is-swap");
  void node.offsetWidth;
  node.classList.add("is-swap");
}

function showLevel(rows, input) {
  rows.forEach(row => swap(row.querySelector("[data-member-price]"), JSON.parse(row.dataset.memberPrices)[input.value]));
}

function prepareLevels(root) {
  const rows = [...root.querySelectorAll("[data-member-prices]")];
  const status = root.querySelector("[data-level-status]");
  const inputs = [...root.querySelectorAll("input[name='member-level']")];
  inputs.forEach(input => input.addEventListener("change", () => {
    showLevel(rows, input);
    if (status) status.textContent = "Уровень «" + input.dataset.levelTitle + "»: скидка " + input.dataset.levelDiscount + "%, цены выпускника пересчитаны.";
  }));
  const chosen = inputs.find(input => input.checked);
  if (chosen) showLevel(rows, chosen);
}

function keepRowsVisible(root) {
  const levels = root.querySelector(".lux-calc__levels");
  root.querySelector(".lux-progs")?.addEventListener("focusin", event => {
    const covered = levels.getBoundingClientRect().bottom - event.target.getBoundingClientRect().top;
    if (covered > 0) scrollBy({ top: -(covered + FOCUS_GAP), behavior: "instant" });
  });
}

function watchHero() {
  watcher?.disconnect();
  if (!hero || !("IntersectionObserver" in window)) return;
  watcher = new IntersectionObserver(([entry]) => hero.classList.toggle("is-off", !entry.isIntersecting));
  watcher.observe(hero);
}

export function prepareHome() {
  const root = document.querySelector(".lux-home");
  hero = root?.querySelector(".lux-hero") || null;
  if (!root || root.dataset.homeReady) { requestHero(); return; }
  root.dataset.homeReady = "true";
  hidePastStarts(root);
  prepareReveal(root);
  prepareLevels(root);
  keepRowsVisible(root);
  watchHero();
  requestHero();
}
