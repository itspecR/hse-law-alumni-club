(() => {
  const VEIL_TIMEOUT = 8000;
  const LIVE_KEYS = { member: "club_token", office: "club_admin_token", cart: "club_cart" };

  function stored(kind) {
    try { return Boolean(LIVE_KEYS[kind] && localStorage.getItem(LIVE_KEYS[kind])); } catch { return false; }
  }

  function veil(root) {
    if (!root.hasAttribute("data-veil") || !stored(root.dataset.live)) return;
    root.dataset.skeleton = "panel";
    setTimeout(() => { delete root.dataset.skeleton; }, VEIL_TIMEOUT);
  }

  veil(document.documentElement);
})();
