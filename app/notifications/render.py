"""Email templates (Jinja2, autoescaped)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)


def render_html(template: str, **context: Any) -> str:
    return _env.get_template(f"{template}.html.j2").render(**context)
