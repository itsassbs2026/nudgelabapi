"""The agent servers and how many calls they can take (2026-10-06, five servers; the app's busy check).

Each agent server writes its own row every 30 seconds (the agent repo's ops/heartbeat.py): `accepting` is
false while it drains or is stopping. A row not refreshed for a while is a server that's off. The API sums the
capacity of the fresh, accepting rows and compares it with the calls live in LiveKit (app/mobile/capacity.py).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UtcDateTime


class AgentServer(Base):
    __tablename__ = "agent_servers"

    server_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # main or worker
    capacity: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # calls it takes comfortably
    accepting: Mapped[bool] = mapped_column(Boolean, nullable=False)
    release_commit: Mapped[str | None] = mapped_column(String(40))
    last_seen: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
