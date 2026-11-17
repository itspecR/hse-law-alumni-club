const ERROR_ID_PREFIX = "form-error-";
let errorCount = 0;

function usable(control) {
  return control instanceof HTMLElement && control.type !== "hidden" && !control.closest("[aria-hidden='true'],[hidden]");
}

function labelText(control) {
  const label = control.labels?.[0];
  const own = label ? [...label.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join(" ") : "";
  return (own || control.getAttribute("aria-label") || control.name).replace(/\s+/g, " ").trim();
}

function errorId(box) {
  if (!box.id) box.id = ERROR_ID_PREFIX + ++errorCount;
  return box.id;
}

export function invalidControl(form, field) {
  if (!field) return null;
  const found = form.elements.namedItem(field);
  const control = found instanceof RadioNodeList ? found[0] : found;
  return usable(control) ? control : null;
}

export function fieldMessage(control) {
  return "Проверьте поле «" + labelText(control) + "».";
}

export function markInvalid(control, box) {
  control.setAttribute("aria-invalid", "true");
  control.setAttribute("aria-describedby", [control.getAttribute("aria-describedby"), errorId(box)].filter(Boolean).join(" "));
  control.dataset.errorLinked = box.id;
  control.focus();
}

export function clearInvalid(form) {
  for (const control of form.querySelectorAll("[data-error-linked]")) {
    const ids = (control.getAttribute("aria-describedby") || "").split(" ").filter(id => id && id !== control.dataset.errorLinked);
    if (ids.length) control.setAttribute("aria-describedby", ids.join(" "));
    else control.removeAttribute("aria-describedby");
    control.removeAttribute("aria-invalid");
    delete control.dataset.errorLinked;
  }
}
