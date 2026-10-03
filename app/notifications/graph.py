"""Microsoft Graph email (SPEC §9; pingit §10): client-credentials flow, `Mail.Send` restricted to one sender
mailbox. Only ever called by the worker, never inside a request."""

from __future__ import annotations

import httpx
import msal

from app.config import Settings

GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class GraphMailError(Exception):
    """Any Graph auth or send failure. The worker records it on the outbox row and retries."""


_apps: dict[tuple[str, str, str, str], msal.ConfidentialClientApplication] = {}


def _access_token(settings: Settings) -> str:
    assert settings.graph_tenant_id and settings.graph_client_id and settings.graph_client_secret
    key = (
        settings.graph_authority_host,
        settings.graph_tenant_id,
        settings.graph_client_id,
        settings.graph_client_secret,
    )
    app = _apps.get(key)
    if app is None:  # msal caches the token inside the app instance until it expires
        app = msal.ConfidentialClientApplication(
            client_id=settings.graph_client_id,
            client_credential=settings.graph_client_secret,
            authority=f"{settings.graph_authority_host}/{settings.graph_tenant_id}",
        )
        _apps[key] = app
    result = app.acquire_token_for_client(scopes=[GRAPH_SCOPE])
    if not result or "access_token" not in result:
        raise GraphMailError(
            f"Graph token request failed: {(result or {}).get('error_description', 'unknown')}"
        )
    return str(result["access_token"])


def send_email(settings: Settings, to_email: str, subject: str, body_html: str) -> None:
    if not settings.graph_configured:
        raise GraphMailError("Microsoft Graph is not configured (EMAIL_ENABLED or credentials missing).")
    url = f"{settings.graph_base_url}/users/{settings.graph_sender_mailbox}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": to_email}}],
        },
        "saveToSentItems": False,
    }
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {_access_token(settings)}"},
            timeout=settings.graph_request_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise GraphMailError(f"Graph sendMail request failed: {exc}") from exc
    if response.status_code not in (200, 202):
        raise GraphMailError(f"Graph sendMail failed with HTTP {response.status_code}: {response.text[:300]}")
