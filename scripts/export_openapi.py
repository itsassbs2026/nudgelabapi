"""Write the API's OpenAPI schema to openapi.json, for the dashboard's generated types (SPEC §3.2).

    .venv/Scripts/python scripts/export_openapi.py
    (then in nudgelabdashboard: npm run gen:api)

Building the schema needs no database: placeholder settings are used when none are set.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "mysql+pymysql://schema-only@localhost/none")
os.environ.setdefault("JWT_SECRET", "schema-export-placeholder-not-used-for-signing")
os.environ.setdefault("APP_ENV", "alpha")

from app.main import create_app  # noqa: E402

if __name__ == "__main__":
    schema = create_app().openapi()
    text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
    (ROOT / "openapi.json").write_text(text, encoding="utf-8")
    print(f"openapi.json: {len(schema['paths'])} paths")
