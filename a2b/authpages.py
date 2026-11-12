"""Server-rendered pages for signing in, signing up and resetting a password.

Why these are pages and not the modal they replace. A modal has no address, and
everything about this flow needs one: a reset link has to arrive from an email
and land somewhere; a person who was interrupted has to be able to come back;
a password manager wants a page it can recognise and offer to fill; and support
needs to be able to say "go to a2b.lenni.io/login" out loud.

The markup is deliberately plain — real `<form>` elements, real labels, and the
`autocomplete` tokens browsers look for (`username`, `current-password`,
`new-password`). Password managers are the main reason people have distinct
passwords at all, and they only help if the form is legible to them.

Submission goes through the JSON API rather than form-POST handlers so there is
one implementation of each rule. The pages are the view; `a2b.auth` is the
truth.
"""
from __future__ import annotations

from .config import PUBLIC_BASE

BRAND = "Lenni A2B"

# Where to land after signing in, when nothing else was asked for.
HOME = "/console/"


def safe_next(raw: str) -> str:
    """Sanitise a `?next=` path.

    Reflecting this unchecked is the classic open redirect: an attacker sends
    `/login?next=https://evil.example`, the victim signs in on our real page,
    and we hand them to a convincing copy that asks them to sign in again.

    Only site-relative paths survive. `//host` is rejected because a browser
    reads a protocol-relative URL as another origin, and anything with a colon
    is rejected to keep `javascript:` and friends out.
    """
    value = (raw or "").strip()
    if not value.startswith("/") or value.startswith("//") or ":" in value:
        return HOME
    return value


_CSS = """
:root{--navy:#203048;--navy-d:#16223a;--em:#209D76;--em-d:#177a5b;--mint-l:#e2f5ee;
--ink:#22304a;--muted:#64748b;--line:#dde5ee;--paper:#f6f9fb;--card:#fff;--red:#b04a2d;--red-bg:#fde8e2}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;color:var(--ink);
background:var(--paper);line-height:1.55;-webkit-font-smoothing:antialiased;
min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
a{color:var(--em-d)}
.card{background:var(--card);width:100%;max-width:420px;border:1px solid var(--line);
border-radius:14px;padding:34px 32px;box-shadow:0 18px 40px -28px rgba(32,48,72,.45)}
.brand{display:flex;align-items:center;gap:9px;margin-bottom:22px}
.brand .mark{width:26px;height:26px;border-radius:7px;background:var(--em);flex:none}
.brand .txt{font-weight:700;color:var(--navy);letter-spacing:-.01em}
h1{font-size:21px;color:var(--navy);line-height:1.25;margin-bottom:6px}
.sub{color:var(--muted);font-size:14px;margin-bottom:22px}
label{display:block;font-size:12.5px;font-weight:600;color:var(--navy);
text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
input{width:100%;padding:11px 13px;border:1px solid var(--line);border-radius:9px;
font-size:15px;font-family:inherit;color:var(--ink);background:#fff}
input:focus{outline:none;border-color:var(--em);box-shadow:0 0 0 3px var(--mint-l)}
.field{margin-bottom:16px}
.btn{width:100%;padding:12px;border:0;border-radius:9px;background:var(--em-d);color:#fff;
font-size:15px;font-weight:600;font-family:inherit;cursor:pointer}
.btn:hover{background:#12614a}
.btn[disabled]{opacity:.6;cursor:default}
.err{background:var(--red-bg);color:var(--red);border-radius:9px;padding:10px 12px;
font-size:13.5px;margin-bottom:16px;display:none}
.err.show{display:block}
.ok{background:var(--mint-l);color:var(--em-d);border-radius:9px;padding:14px;
font-size:14px;margin-bottom:16px}
.alt{margin-top:20px;padding-top:18px;border-top:1px solid var(--line);
font-size:13.5px;color:var(--muted);display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.hint{font-size:12.5px;color:var(--muted);margin-top:6px}
"""


def _shell(title: str, body: str, *, extra_head: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} &middot; {BRAND}</title>
<meta name="robots" content="noindex,nofollow">
{extra_head}
<style>{_CSS}</style>
</head>
<body>
<main class="card">
  <div class="brand"><span class="mark"></span><span class="txt">{BRAND}</span></div>
  {body}
</main>
</body>
</html>"""


# The one piece of shared client script: post JSON, show the server's message on
# failure, follow the redirect on success. Kept here rather than in a static
# file so a sign-in page is a single request with nothing to go stale.
_SUBMIT_JS = """
function wire(formId, url, onOk){
  var form = document.getElementById(formId);
  var err  = document.getElementById('err');
  var btn  = form.querySelector('button[type=submit]');
  form.addEventListener('submit', function(e){
    e.preventDefault();
    err.className = 'err';
    btn.disabled = true;
    var body = {};
    new FormData(form).forEach(function(v,k){ body[k] = v; });
    fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                credentials:'same-origin', body:JSON.stringify(body)})
      .then(function(r){ return r.json().then(function(d){ return {ok:r.ok, d:d}; }); })
      .then(function(res){
        if(res.ok){ onOk(res.d); return; }
        err.textContent = (res.d && res.d.detail) || 'Something went wrong. Try again.';
        err.className = 'err show';
        btn.disabled = false;
      })
      .catch(function(){
        err.textContent = 'Could not reach the server. Check your connection.';
        err.className = 'err show';
        btn.disabled = false;
      });
  });
}
"""


def login_page(next_path: str = "") -> str:
    target = safe_next(next_path)
    body = f"""
  <h1>Sign in</h1>
  <p class="sub">Your workspaces, and the services you publish from them.</p>
  <div id="err" class="err"></div>
  <form id="f" novalidate>
    <div class="field">
      <label for="email">Work email</label>
      <input id="email" name="email" type="email" autocomplete="username"
             autocapitalize="none" spellcheck="false" required autofocus>
    </div>
    <div class="field">
      <label for="password">Password</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" required>
    </div>
    <button class="btn" type="submit">Sign in</button>
  </form>
  <div class="alt">
    <a href="/forgot-password">Forgot your password?</a>
    <a href="/signup">Create an account</a>
  </div>
<script>{_SUBMIT_JS}
wire('f','/api/auth/login',function(){{ window.location.href={target!r}; }});
</script>"""
    return _shell("Sign in", body)


def signup_page(next_path: str = "") -> str:
    target = safe_next(next_path)
    body = f"""
  <h1>Create your account</h1>
  <p class="sub">One account, then as many workspaces as you run.</p>
  <div id="err" class="err"></div>
  <form id="f" novalidate>
    <div class="field">
      <label for="name">Your name</label>
      <input id="name" name="name" autocomplete="name" required autofocus>
    </div>
    <div class="field">
      <label for="email">Work email</label>
      <input id="email" name="email" type="email" autocomplete="username"
             autocapitalize="none" spellcheck="false" required>
      <p class="hint">If someone has invited you to a workspace, use that address
      and the invitation becomes access straight away.</p>
    </div>
    <div class="field">
      <label for="password">Password</label>
      <input id="password" name="password" type="password"
             autocomplete="new-password" minlength="10" required>
      <p class="hint">At least 10 characters. Length beats punctuation.</p>
    </div>
    <button class="btn" type="submit">Create account</button>
  </form>
  <div class="alt">
    <span>Already have one?</span>
    <a href="/login">Sign in</a>
  </div>
<script>{_SUBMIT_JS}
wire('f','/api/auth/signup',function(){{ window.location.href={target!r}; }});
</script>"""
    return _shell("Create your account", body)


def forgot_page() -> str:
    body = f"""
  <h1>Reset your password</h1>
  <p class="sub">We will email you a link to choose a new one.</p>
  <div id="err" class="err"></div>
  <div id="done" style="display:none">
    <div class="ok">
      <strong>Check your email.</strong><br>
      If that address has an account, a reset link is on its way. It works once
      and expires in an hour.
    </div>
    <div class="alt"><a href="/login">Back to sign in</a></div>
  </div>
  <form id="f" novalidate>
    <div class="field">
      <label for="email">Work email</label>
      <input id="email" name="email" type="email" autocomplete="username"
             autocapitalize="none" spellcheck="false" required autofocus>
    </div>
    <button class="btn" type="submit">Email me a link</button>
  </form>
  <div id="alt" class="alt">
    <a href="/login">Back to sign in</a>
    <a href="/signup">Create an account</a>
  </div>
<script>{_SUBMIT_JS}
wire('f','/api/auth/forgot',function(){{
  document.getElementById('f').style.display='none';
  document.getElementById('alt').style.display='none';
  document.getElementById('done').style.display='block';
}});
</script>"""
    return _shell("Reset your password", body)


def reset_page(token: str, valid: bool) -> str:
    """The page a reset link lands on.

    `Referrer-Policy: no-referrer` is set here specifically: the token is in the
    query string, and without it any third-party request this page made would
    carry the full URL — and therefore a live credential — in the Referer
    header.
    """
    head = '<meta name="referrer" content="no-referrer">'
    if not valid:
        body = """
  <h1>That link has expired</h1>
  <p class="sub">Reset links work once and last an hour.</p>
  <div class="ok">Ask for a fresh one and it will arrive in a moment.</div>
  <div class="alt">
    <a href="/forgot-password">Send a new link</a>
    <a href="/login">Back to sign in</a>
  </div>"""
        return _shell("Link expired", body, extra_head=head)

    body = f"""
  <h1>Choose a new password</h1>
  <p class="sub">This signs you out of every other browser.</p>
  <div id="err" class="err"></div>
  <form id="f" novalidate>
    <input type="hidden" name="token" value="{token}">
    <div class="field">
      <label for="password">New password</label>
      <input id="password" name="password" type="password"
             autocomplete="new-password" minlength="10" required autofocus>
      <p class="hint">At least 10 characters, and not built out of your email.</p>
    </div>
    <button class="btn" type="submit">Set password and sign in</button>
  </form>
<script>{_SUBMIT_JS}
wire('f','/api/auth/reset',function(){{ window.location.href={HOME!r}; }});
</script>"""
    return _shell("Choose a new password", body, extra_head=head)
