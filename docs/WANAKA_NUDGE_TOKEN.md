# Wanaka task: `POST /v1/nudge/token` (the NudgeLab pass)

> **For:** Claude Code working in the Wanaka repo (`C:\python\wanaka`), and the Wanaka owner.
> **Why:** the Flutter app's **Nudge** menu is moving from Wanaka to NudgeLab's API (`nudgelabapi`). The app keeps
> signing in with Wanaka. When the user opens Nudge, the app exchanges its Wanaka token for a short-lived
> **NudgeLab pass**, and uses that pass with `nudgelabapi`. This endpoint is the only Wanaka change.
>
> Wanaka's own sign-in, tokens, `SECRET_KEY` and existing routes stay exactly as they are.

## 1. What to build

One new route, `POST /v1/nudge/token`, in a new module `nudge_token.py`. Mount it the same way
`ai_trainer_assignments.py` is mounted (via `career_router.include_router`).

**Authentication:** the same dependency the Flutter app's existing protected routes use with the token from
`POST /v1/login-portal`.
- Check whether `/v1/login-portal` records the token with `upsert_expiry_token`.
  - If it does, use `verify_jwt_primetwok` (it also enforces "latest token only").
  - If it doesn't, use `verify_jwt_basic`. `verify_jwt_primetwok` would reject every app token.
- **Tell the owner which one you chose, and why.**
- The token's `uid` claim is required and must be an integer. Otherwise return 401 `"Token missing uid"`.

**Request body:** none.

**Response (200):**

```json
{"token": "<NudgeLab pass>", "token_type": "Bearer", "expires_in": 900}
```

**The pass** is a JWT signed with **ES256** (python-jose, already in `requirements.txt` with `cryptography`):

| Part | Value |
|---|---|
| header `alg` | `ES256` |
| header `kid` | value of `NUDGE_PASS_KID` |
| `iss` | `"wanaka"` |
| `aud` | `"nudgelabapi"` |
| `sub` | the uid **as a string**, e.g. `"53"` |
| `iat` | now, integer seconds (UTC) |
| `exp` | `iat + 900` |
| `jti` | `secrets.token_urlsafe(16)` |

No other claims: no email, no name. nudgelabapi looks up everything else itself.

Signing call: `jwt.encode(claims, private_key_pem, algorithm="ES256", headers={"kid": kid})`.

## 2. Configuration

Two new `.env` keys. Don't add them to `config.py` defaults with real values.

| Key | Meaning |
|---|---|
| `NUDGE_PASS_PRIVATE_KEY_FILE` | absolute path to the private key PEM, **outside the repo** |
| `NUDGE_PASS_KID` | key id, e.g. `nudge-2026-10` |

- Read the key file on first use and cache it.
- If either key is missing or the file can't be read, the route returns **503** `"Nudge pass not configured"` and
  logs the reason once. **Never crash Wanaka's startup over this:** every other route must keep working.

## 3. Rules

- Don't change `SECRET_KEY`, `ALGORITHM`, `auth.py`'s existing functions, the login routes, or any other route.
- Never log the token or the key. Log one line per pass issued: `uid`, `jti`, `kid`.
- The private key never goes into git. Add `*.pem` to `.gitignore` if it's not already there.
- No database calls are needed in this route.

## 4. Self-check (before deploy)

Add `scripts/check_nudge_pass.py`. It should:
1. Generate a throwaway ES256 key pair in memory, with no files.
2. Call the module's signing function with uid 53.
3. Decode the result with the public key, `audience="nudgelabapi"` and `issuer="wanaka"`, and print the header and
   claims.
4. Confirm that decoding fails with a wrong audience, and fails with a different public key.

Also run the app locally and check:
- the route needs a token (401 without one)
- a valid app token returns 200 with the shape above
- 503 when the key isn't configured

## 5. Owner steps

**Create the key pair once, on your own machine or the Wanaka server, not in the repo:**

```bash
openssl ecparam -name prime256v1 -genkey -noout -out nudge_pass_private.pem
openssl ec -in nudge_pass_private.pem -pubout -out nudge_pass_public.pem
```

**On the Wanaka server:**
1. Put `nudge_pass_private.pem` outside the repo.
2. `chmod 600` it and make it owned by the user `wanaka.service` runs as.
3. Set `NUDGE_PASS_PRIVATE_KEY_FILE` and `NUDGE_PASS_KID` in Wanaka's `.env`.
4. Deploy as usual (`deploy/BRINGUP_RUNBOOK.md`) and restart `wanaka.service`.

**For nudgelabapi:** give the NudgeLab side `nudge_pass_public.pem` and the `kid`. The public key isn't secret;
it can only check passes, not make them.

**Rotation (later):**
1. Make a new pair with a new `kid`.
2. Add its public key to nudgelabapi; it accepts both.
3. Switch Wanaka's `.env` to the new private key.
4. Remove the old public key after 15 minutes.

## 6. Not part of this task

Report these to the owner, and don't change them unless asked:
- `POST /v1/login` and `POST /v1/token` issue tokens from email + uid without a password.
- `GET /v1/trainer-assignments/{uid}` doesn't check that the token's uid matches the path.

Both routes retire when the app moves to NudgeLab.
