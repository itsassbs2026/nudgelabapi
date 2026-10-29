# Training in the employee portal

> Built 2026-10-29. Employees take NudgeLab voice trainings from the employee portal (Drupal 7, myprimeportal.com
> and alpha.myprimeportal.com) as well as the Flutter app. Owner decisions: same uid everywhere, all employees,
> the call runs on a NudgeLab-hosted page (training.myprimeportal.com), not inside Drupal.

## How it fits together

```
 Portal (Drupal 7)                      NudgeLab API                       training.myprimeportal.com
 ───────────────────                    ────────────────                   ───────────────────────────
 My training page  ── pass (uid) ──▶    GET /app/v1/trainings
 Start tap         ── pass (uid) ──▶    POST /app/v1/handoffs  ──▶ code
 302 to the page with ?h=<code>  ───────────────────────────────────────▶  POST /app/v1/handoffs/redeem
                                        ◀── LiveKit token, title ────────  Begin tap → LiveKit room → Anne
```

- **The pass.** The module signs a NudgeLab pass (ES256 JWT, `iss: "portal"`, `sub: <uid>`, 5 minutes) with the
  portal's private key. The API verifies it with the public key listed in `APP_PASS_PUBLIC_KEYS` (kid
  `portal-1`), exactly as it verifies Wanaka's passes for the app (`app/mobile/passes.py`). The pass never
  reaches the browser.
- **The handoff** (`app/mobile/handoff.py`, table `app_handoffs`, migration 0024). Start asks for a random code
  that works **once**, for **90 seconds**, for that employee and that training. Only its SHA-256 is stored. The
  page removes it from the address as soon as it loads. `return_url` is kept only on a portal address
  (`PORTAL_ORIGINS`), so the page can never be used to send someone elsewhere.
- **The session** is started by the same code as the app's (`_start` in `app/routers/app_trainings.py`):
  assignment check, trainer persona, capacity check, `app_session_starts` row. The agent sees
  `client: "portal"` in the metadata, so reports can tell portal sessions apart from the app (`flutter`).
- **The page** is a second entry in the dashboard build (`training.html`, `src/training/` in nudgelabdashboard),
  served by its own nginx site (`deploy/nginx-training.conf`). It does what the tester page does: captions, the
  trainer's state, topic images (`nudge.media`), keeping the screen on, and rejoining after a dropped connection.

## Settings (API `.env`)

| Setting | Default | |
|---|---|---|
| `APP_PASS_PUBLIC_KEYS` | (Wanaka's) | add `portal-1=/srv/nudgelabapi/keys/portal-pass.pub.pem` |
| `APP_PASS_ISSUER` | `wanaka,portal` | who may sign; a pass still needs a listed key |
| `CORS_ORIGINS` | | add `https://training.myprimeportal.com` |
| `TRAINING_PAGE_URL` | `https://training.myprimeportal.com` | where Start links point |
| `PORTAL_ORIGINS` | myprimeportal.com, www, alpha | where the page may send people back to |
| `PORTAL_HANDOFF_SECONDS` | 90 | how long a Start link works |
| `PORTAL_REDEEM_RATE_LIMIT` | 20/minute | per address |

## The Drupal module

`clients/drupal7/nudgelab_training/` (README.txt has the install steps). PHP 7.2+, OpenSSL and cURL. Pages:
`/my-training` (list: to do first, overdue first, then completed), `/my-training/start/<training>` (CSRF token,
then the 302), settings at `admin/config/services/nudgelab-training`, and a block "trainings to do" (cached two
minutes per employee).

Tested 2026-10-29 on a local Drupal 7.103 against a local API: sign-in, the list, Start → 302 to the training
page with a code, the code redeemed once (a second try gets 410), a tampered Start link refused, and 50
PHP-signed passes verified by the API's own code. Not yet tested: the portal's real theme, the real API, iPhone
Safari.
