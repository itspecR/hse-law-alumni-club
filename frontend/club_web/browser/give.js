const WAIT_LABEL = "Подождите…";
const NO_PAYMENT_LINK = "Не удалось получить ссылку на оплату. Попробуйте позже.";
const NO_TRANSFER = "Не удалось получить реквизиты для перевода. Попробуйте позже.";
const QR_IMAGE = "data:image/svg+xml";
const QR_FILE = "data:image/png;base64,";

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

function chosenMethod(form) {
  return form.elements.namedItem("method").value;
}

function render(form) {
  const rubles = chosenRubles(form);
  swap(form.querySelector("[data-give-value]"), grouped(rubles));
  form.querySelector("[data-give-label]").textContent = grouped(rubles) + " ₽";
  form.querySelectorAll(".give-seg").forEach(placeThumb);
  form.querySelectorAll("[data-give-hint]").forEach(hint => { hint.hidden = hint.dataset.giveHint !== chosenMethod(form); });
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
    method: chosenMethod(form),
    name: field("name").value.trim(),
    email: field("email").value.trim(),
    consent_offer: field("consent_offer").checked,
    consent_pdn: field("consent_pdn").checked,
    website: field("website").value,
  };
}

function requisiteRow(list, label, value) {
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  list.append(term, detail);
}

function showTransfer(form, number, transfer) {
  const qr = String(transfer?.qr_svg || "");
  const file = String(transfer?.qr_png || "");
  if (!qr.startsWith(QR_IMAGE) || !file.startsWith(QR_FILE) || !Array.isArray(transfer.payee)) throw new Error(NO_TRANSFER);
  const block = form.parentElement.querySelector("[data-give-transfer]");
  const amount = grouped(transfer.amount / 100) + " ₽";
  const list = block.querySelector("[data-transfer-requisites]");
  block.querySelector("[data-transfer-amount]").textContent = amount;
  block.querySelector("[data-transfer-qr]").src = qr;
  Object.assign(block.querySelector("[data-transfer-save]"), { href: file, download: number + ".png" });
  block.querySelector("[data-transfer-number]").textContent = number;
  list.replaceChildren();
  transfer.payee.forEach(item => requisiteRow(list, String(item.label), String(item.value)));
  requisiteRow(list, "Сумма", amount);
  requisiteRow(list, "Назначение платежа", String(transfer.purpose));
  form.hidden = true;
  block.hidden = false;
  block.focus();
}

async function pay(form, api, rubles) {
  const result = await api.request("/donations", "POST", { ...donationBody(form, rubles), captcha: await api.solveCaptcha() });
  if (chosenMethod(form) === "transfer") {
    showTransfer(form, String(result.number), result.transfer);
    return;
  }
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
  form.querySelectorAll("input[name=method]").forEach(input => input.addEventListener("change", () => changeAmount(form)));
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
