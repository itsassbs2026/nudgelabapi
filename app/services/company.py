"""Who this installation is for (2026-10-20): one codebase serves Prime Communications and Suncom Mobile, each
on its own servers and database. The company's name and wording live in that installation's `dash_settings`
(Admin > Settings), defaulting to Prime's, so the AI prompts, new trainings and the dashboard say the right
name.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services import settings_table

# Prime's values: what an installation says until an Admin sets its own (migration 0019 seeds the same).
DEFAULTS = {
    "company.name": "Prime Communications",
    "company.description": "an AT&T authorized retailer",
    "company.app_name": "the Prime app",
    "company.portal_name": "Prime Portal",
    "company.email_domain": "primecomms.com",
}


@dataclass(frozen=True)
class Company:
    name: str
    description: str
    app_name: str
    portal_name: str
    email_domain: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


PRIME = Company(*(DEFAULTS[k] for k in DEFAULTS))


def company(db: Session) -> Company:
    return Company(
        *(str(settings_table.get(db, key, default) or default) for key, default in DEFAULTS.items())
    )
