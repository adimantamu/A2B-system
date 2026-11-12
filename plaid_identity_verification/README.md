# ProofLink — Plaid Identity Verification demo

An isolated concept demo for organization-managed, per-employee identity
verification links. It was called Lenni Verify until it was named ProofLink;
it moves into the Lenni workspace with #141.

## What it demonstrates

1. An administrator creates an organization.
2. The administrator receives a private employee-enrollment link.
3. Each employee who enrolls gets an account and a unique, permanent customer
   verification link.
4. Every visit to an employee's link creates a fresh verification session,
   records the consent text/version, and attributes the session to that employee
   and organization.
5. In safe demo mode, a simulator returns one of three synthetic outcomes:
   passed, needs review, or unable to verify.
6. The employee gets an outcome-only email and can inspect the result after
   signing in. Sensitive identity data is never included in email.

The demo intentionally does **not** collect SSNs, national ID numbers, identity
documents, or selfies. When Plaid mode is configured, those fields are collected
in Plaid's hosted flow rather than by this application.

## Deployment layout

| Path | Purpose |
|---|---|
| `/opt/plaid_identity_verification` | Isolated application and virtualenv |
| `/var/lib/plaid_identity_verification` | SQLite database and development email outbox |
| `/etc/plaid_identity_verification/demo.env` | Instance-only configuration and Plaid credentials |
| `plaid-identity-verification.service` | Uvicorn on `127.0.0.1:8011` |
| `https://a2b-staging.lenni.io/plaid-demo/` | Public TLS route through the existing named tunnel |

The normal `/opt/a2b` rsync deployment cannot overwrite either the app or its
data.

## Local run

```bash
cd plaid_identity_verification
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

PIV_DATA_DIR=/tmp/piv-demo \
PIV_COOKIE_SECURE=0 \
PIV_PUBLIC_BASE=http://127.0.0.1:8011 \
uvicorn app:app --host 127.0.0.1 --port 8011
```

Tests:

```bash
pytest -q
```

## Switching from the simulator to Plaid Sandbox

Identity Verification is disabled in Plaid Sandbox by default. Enable it from
the Plaid Dashboard's Products page, request Template Editor access, and create
an IDV template.

Edit `/etc/plaid_identity_verification/demo.env`:

```dotenv
PIV_PROVIDER=plaid
PLAID_ENV=sandbox
PLAID_CLIENT_ID=...
PLAID_SECRET=...
PLAID_IDV_TEMPLATE_ID=idvtmp_...
```

Then configure this exact URL in **Plaid Dashboard → Developers → Webhooks**:

```text
https://a2b-staging.lenni.io/plaid-demo/webhooks/plaid
```

IDV ignores webhook URLs passed to `/link/token/create`; its webhook is a
Dashboard setting. The receiver:

- verifies the `Plaid-Verification` ES256 JWT,
- verifies the SHA-256 hash of the exact raw body,
- rejects stale signatures,
- treats the webhook only as a signal,
- fetches authoritative state from `/identity_verification/get`, and
- stores a normalized summary without raw PII or biometric media.

Restart:

```bash
sudo systemctl restart plaid-identity-verification
curl -fsS http://127.0.0.1:8011/health
```

## Important limits

- This is an identity-verification concept, not a criminal-background check.
- Plaid Identity Verification output is not a consumer report. It must not be
  used as the sole basis for credit, employment, housing, or insurance decisions.
- The standard Plaid contract may not permit a multi-tenant reseller model.
  Commercial approval or per-customer Plaid accounts are required before this
  becomes a production product.
- SQLite is appropriate for the demo only. Production should use Postgres,
  KMS-backed field encryption, formal retention/deletion policies, and complete
  access auditing.
