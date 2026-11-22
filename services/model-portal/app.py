import hmac
import os
import re
import time
from collections import defaultdict, deque
from urllib.parse import urlencode

import urllib.error
import urllib.request
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(docs_url=None, redoc_url=None)
attempts = defaultdict(deque)
EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")


def limited(ip: str) -> bool:
    now = time.monotonic()
    bucket = attempts[ip]
    while bucket and bucket[0] < now - 600:
        bucket.popleft()
    if len(bucket) >= 8:
        return True
    bucket.append(now)
    return False


def litellm_post(path: str, payload: dict) -> dict:
    body = __import__("json").dumps(payload).encode()
    request = urllib.request.Request(
        os.environ["LITELLM_INTERNAL_URL"].rstrip("/") + path,
        data=body,
        headers={
            "Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return __import__("json").load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise HTTPException(409, "An account with this email already exists.") from exc
        raise HTTPException(503, "The model account service is unavailable.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(503, "The model account service is unavailable.") from exc


def ensure_invitation_principal() -> None:
    base = os.environ["LITELLM_INTERNAL_URL"].rstrip("/")
    user_id = "default_user_id"
    request = urllib.request.Request(
        base + "/user/info?user_id=" + user_id,
        headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"]},
    )
    for attempt in range(30):
        try:
            with urllib.request.urlopen(request, timeout=8):
                return
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                try:
                    litellm_post("/user/new", {
                        "user_id": user_id,
                        "user_role": "proxy_admin",
                        "auto_create_key": False,
                    })
                    return
                except HTTPException as create_error:
                    if create_error.status_code == 409:
                        continue
                    raise
            if exc.code not in {401, 403, 404, 429, 500, 502, 503}:
                raise RuntimeError("Could not initialize LiteLLM's internal invitation principal.") from exc
        except (urllib.error.URLError, TimeoutError):
            pass
        if attempt == 29:
            raise RuntimeError("LiteLLM did not become ready for portal initialization.")
        time.sleep(2)


@app.on_event("startup")
def initialize_litellm():
    ensure_invitation_principal()


@app.get("/register", response_class=HTMLResponse)
def register_page():
    return """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Local AI access</title><style>body{font:16px system-ui;max-width:34rem;margin:8vh auto;padding:0 1rem;background:#111;color:#eee}input,button{box-sizing:border-box;width:100%;padding:.8rem;margin:.4rem 0;border-radius:.5rem;border:1px solid #555;background:#222;color:#fff}button{background:#6750a4;border:0;cursor:pointer}small{color:#aaa}</style></head><body><h1>Get local model access</h1><p>Create a personal model account. Your account can create its own API keys for OpenHands on your computer.</p><form action="/api/register" method="post"><label>Email<input name="email" type="email" required maxlength="254" autocomplete="email"></label><label>Registration code<input name="code" type="password" required autocomplete="one-time-code"></label><button>Create account</button></form><small>After registration, set a password and create your personal key on the LiteLLM page.</small></body></html>"""


@app.post("/api/register")
def register(request: Request, email: str = Form(...), code: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    if limited(ip):
        raise HTTPException(429, "Too many registration attempts. Try again in ten minutes.")
    email = email.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(400, "Enter a valid email address.")
    expected = os.environ["MODEL_SIGNUP_CODE"]
    if not hmac.compare_digest(code.encode(), expected.encode()):
        raise HTTPException(403, "The registration code is invalid.")
    user = litellm_post("/user/new", {
        "user_email": email,
        "user_role": "internal_user",
        "models": ["local-model"],
        "max_budget": 5,
        "auto_create_key": False,
    })
    invitation = litellm_post("/invitation/new", {"user_id": user["user_id"]})
    url = os.environ["BACKEND_PUBLIC_URL"].rstrip("/") + "/ui/onboarding?" + urlencode({"id": invitation["id"]})
    return RedirectResponse(url, status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}
