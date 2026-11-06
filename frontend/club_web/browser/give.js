const WAIT_LABEL = "Подождите…";
const NO_PAYMENT_LINK = "Не удалось получить ссылку на оплату. Попробуйте позже.";

function grouped(number) {
  return Math.round(number).toLocaleString("ru-RU").replace(/\s/g, " ");
}

function swap(node, text) {
  if (node.textContent === text) return;
  node.textContent = text;
  node.classList.remove("is-swap");
  void node.offsetWidth;
  node.classList.add("is-swap");
}

function placeThumb(group) {
  const inputs = [...group.querySelectorAll("input")];
  const index = inputs.findIndex(input => input.checked);
  group.classList.toggle("is-free", index < 0);
  if (index >= 0) group.style.setProperty("--seg-i", String(index));
}

function typedRubles(field) {
  const digits = field.value.replace(/\D/g, "");
  return digits ? Number(digits) : 0;
}

function chosenRubles(form) {
  const typed = typedRubles(form.querySelector("[data-give-other]"));
  if (typed) return typed;
  const preset = form.querySelector("input[name=preset]:checked");
  return preset ? Number(preset.value) / 100 : 0;
}

function render(form) {
  const rubles = chosenRubles(form);
  swap(form.querySelector("[data-give-value]"), grouped(rubles));
  form.querySelector("[data-give-label]").textContent = grouped(rubles) + " ₽";
  form.querySelectorAll(".give-seg").forEach(placeThumb);
}

function amountError(form, rubles) {
  const min = Number(form.dataset.min);
  const max = Number(form.dataset.max);
  if (rubles >= min && rubles <= max) return "";
  return "Сумма пожертвования от " + grouped(min) + " до " + grouped(max) + " ₽.";
}

function donationBody(form, rubles) {
  const field = name => form.elements.namedItem(name);
  return {
    amount: rubles,
    name: field("name").value.trim(),
    email: field("email").value.trim(),
    consent_offer: field("consent_offer").checked,
    consent_pdn: field("consent_pdn").checked,
    website: field("website").value,
  };
}

async function pay(form, api, rubles) {
  const result = await api.request("/donations", "POST", { ...donationBody(form, rubles), captcha: await api.solveCaptcha() });
  const payment = api.safeLink(result.payment_url, true);
  if (!payment) throw new Error(NO_PAYMENT_LINK);
  location.assign(payment);
}

async function submit(form, api) {
  const button = form.querySelector("[data-give-submit]");
  form.querySelector("[data-error]").hidden = true;
  if (button.disabled || !form.reportValidity()) return;
  const rubles = chosenRubles(form);
  const error = amountError(form, rubles);
  if (error) {
    api.formError(form, error);
    return;
  }
  const label = button.innerHTML;
  button.disabled = true;
  button.textContent = WAIT_LABEL;
  try { await pay(form, api, rubles); }
  catch (failure) {
    api.formError(form, failure.message);
    button.innerHTML = label;
    button.disabled = false;
  }
}

function changeAmount(form) {
  form.querySelector("[data-error]").hidden = true;
  render(form);
}

function prepareForm(form, api) {
  const other = form.querySelector("[data-give-other]");
  form.querySelectorAll("input[name=preset]").forEach(input => input.addEventListener("change", () => {
    other.value = "";
    changeAmount(form);
  }));
  other.addEventListener("input", () => {
    if (typedRubles(other)) form.querySelectorAll("input[name=preset]").forEach(input => { input.checked = false; });
    changeAmount(form);
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    void submit(form, api);
  });
  render(form);
}

export function prepareGive(api) {
  const form = document.querySelector("[data-give-form]");
  if (!form || form.dataset.giveReady) return;
  form.dataset.giveReady = "true";
  prepareForm(form, api);
}
