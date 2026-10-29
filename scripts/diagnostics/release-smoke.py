import base64, json, os, re, secrets, time, urllib.request, urllib.error, urllib.parse, uuid
from pathlib import Path

root = Path(os.environ["RELEASE_QA_DIR"])
env = dict(line.split("=", 1) for line in (root / "runtime.env").read_text().splitlines() if line and not line.startswith("#"))
base = "http://127.0.0.1:8099/api"
mail = "http://127.0.0.1:8049"
assert env["POSTGRES_DB"] == "club_release" and env["SMTP_HOST"] == "mail", "Only isolated release QA is allowed"
report = []
def request(path, data=None, token=None, method=None, headers=None, expected=200):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token: h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, None if data is None else json.dumps(data).encode(), h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as res: status, raw = res.status, res.read()
    except urllib.error.HTTPError as err: status, raw = err.code, err.read()
    assert status == expected, f"{method or ('POST' if data is not None else 'GET')} {path}: expected {expected}, got {status}"
    return json.loads(raw) if raw else None

def check(name):
    report.append(name); print("PASS " + name, flush=True)

def email_token(recipient, purpose):
    for _ in range(30):
        messages = json.load(urllib.request.urlopen(mail + "/api/v1/messages"))["messages"]
        for item in messages:
            if not any(to.get("Address") == recipient for to in item["To"]): continue
            full = json.load(urllib.request.urlopen(mail + "/api/v1/message/" + item["ID"]))
            match = re.search(r"/" + purpose + r"\?token=([^\s<]+)", full.get("Text", ""))
            if match: return urllib.parse.unquote(match.group(1).split("&")[0])
        time.sleep(.3)
    raise AssertionError("Local confirmation/reset email missing")

assert request("/ready")["status"] == "ok"
request("/admin/overview", expected=401)
admin = request("/auth/admin-login", {"email": env["ADMIN_EMAIL"], "password": env["ADMIN_PASSWORD"]})["token"]
check("readiness and admin authentication")
email = "release-" + uuid.uuid4().hex[:10] + "@release.example.com"
password = secrets.token_hex(16)
body = {"fio": "Проверка релиза", "email": email, "password": password, "cohort": "2020", "edu_level": "магистратура", "edu_program": "Право", "consent_pdn": True}
assert request("/auth/register", body)["confirm_required"]
request("/auth/login", {"email": email, "password": password}, expected=403)
confirm = email_token(email, "confirm")
assert request("/auth/confirm", {"token": confirm})["ok"]
user = request("/auth/login", {"email": email, "password": password})["token"]
assert request("/me", token=user)["alumni"]["verification_status"] == "pending"
request("/admin/overview", token=user, expected=401)
check("registration, SMTP confirmation, pending cabinet and role isolation")
members = request("/admin/members", token=admin)
rows = members if isinstance(members, list) else members["items"]
member_id = json.loads(base64.urlsafe_b64decode(user.split(".")[1] + "=="))["alumni_id"]
member = next(x for x in rows if x["id"] == member_id)
request("/admin/members/" + member["id"], {"verification_status": "verified"}, admin, "PATCH")
assert request("/me", token=user)["alumni"]["verification_status"] == "verified"
request("/me/profile", {"fio": "Проверка готового кабинета", "contacts": {"email": email}}, user, "PATCH")
assert request("/me", token=user)["alumni"]["fio"] == "Проверка готового кабинета"
check("admin verification and persisted profile editing")
product = request("/admin/products", {"title": "Тест релиза " + uuid.uuid4().hex[:8], "category": "merch", "price": 10000, "stock": 2, "status": "published"}, admin)
h = {"X-Cart-Session": str(uuid.uuid4()), "Idempotency-Key": str(uuid.uuid4())}
request("/cart", {"type": "merch", "ref_id": product["slug"], "qty": 1}, user, headers=h)
order = {"contact_fio": "Проверка релиза", "contact_phone": "+70000000000", "contact_email": email, "fulfillment": "pickup", "consent_pdn": True}
receipt = request("/orders", order, user, headers=h)
assert request("/orders", order, user, headers=h) == receipt
saved = next(x for x in request("/admin/orders", token=admin)["items"] if x["number"] == receipt["number"])
request("/admin/orders/" + saved["id"], {"status": "in_progress"}, admin, "PATCH")
assert any(x["number"] == receipt["number"] and x["status"] == "in_progress" for x in request("/me/orders", token=user))
check("catalog, cart, idempotent checkout and admin status visible in cabinet")
cfg = request("/support/config"); assert cfg["enabled"]
ticket, key = str(uuid.uuid4()), secrets.token_hex(32)
request("/support", {"id": ticket, "key": key, "topic": "other", "message": "Локальная проверка поддержки", "consent": True, "consentVersion": cfg["version"]})
request("/support/" + ticket, headers={"X-Support-Key": secrets.token_hex(32)}, expected=404)
request("/admin/support/" + ticket, {"status": "answered", "message": "Ответ поддержки"}, admin, "PATCH")
assert request("/support/" + ticket, headers={"X-Support-Key": key})["status"] == "answered"
check("support ticket privacy and office reply")
request("/auth/forgot", {"email": email})
reset = email_token(email, "reset")
new_password = secrets.token_hex(16)
request("/auth/reset", {"token": reset, "password": new_password})
request("/auth/reset", {"token": reset, "password": new_password}, expected=400)
request("/me", token=user, expected=401)
user = request("/auth/login", {"email": email, "password": new_password})["token"]
check("SMTP password reset, replay rejection and old-session revocation")
request("/admin/analytics?range=7d", token=admin)
health = request("/admin/system-health", token=admin)
assert all(c["status"] == "ok" for c in health["checks"] if c["id"] in ("api", "cms", "database"))
request("/auth/admin-logout", {}, admin)
request("/admin/overview", token=admin, expected=401)
check("analytics, dependency health and admin logout")
credentials = {"email": email, "password": new_password, "admin_token": admin, "reset_token": reset, "member_id": member["id"]}
f = root / "smoke-session.json"; f.write_text(json.dumps(credentials)); f.chmod(0o600)
(root / "smoke-result.json").write_text(json.dumps({"checks": report}, ensure_ascii=False, indent=2))
