"""Choices for the global filter dropdowns (SPEC §7.1): trainings, the org hierarchy, setups and voices."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.reference.agent_tables import (
    training_profiles,
    training_voices,
    trainings,
    vw_trainees,
    vw_training_stores,
)


def filter_options(db: Session, *, district_id: int | None = None) -> dict[str, Any]:
    """Regions, markets and districts come from active stores.

    Stores are listed only for one district, when `district_id` is given (there are about 2,000).
    """
    st = vw_training_stores.c
    regions: dict[int, str] = {}
    markets: dict[int, dict[str, Any]] = {}
    districts: dict[int, dict[str, Any]] = {}
    for r in db.execute(
        select(st.region_id, st.region_name, st.market_id, st.market_name, st.district_id, st.district_name)
        .where(st.store_active == 1)
        .distinct()
    ):
        if r.region_id is not None:
            regions[r.region_id] = r.region_name
        if r.market_id is not None:
            markets[r.market_id] = {"id": r.market_id, "name": r.market_name, "region_id": r.region_id}
        if r.district_id is not None:
            districts[r.district_id] = {
                "id": r.district_id,
                "name": r.district_name,
                "market_id": r.market_id,
            }
    stores = []
    if district_id is not None:
        stores = [
            {"id": r.store_id, "name": r.store_name}
            for r in db.execute(
                select(st.store_id, st.store_name)
                .where(st.district_id == district_id)
                .order_by(st.store_name)
            )
        ]
    return {
        "trainings": [
            {"id": r.training_id, "title": r.title, "completion_type": r.completion_type, "status": r.status}
            # Trainings still being written in the studio (never published) have nothing to report yet.
            for r in db.execute(
                select(trainings).where(trainings.c.status != "draft").order_by(trainings.c.title)
            )
        ],
        "regions": [{"id": k, "name": v} for k, v in sorted(regions.items(), key=lambda kv: str(kv[1]))],
        "markets": sorted(markets.values(), key=lambda m: str(m["name"])),
        "districts": sorted(districts.values(), key=lambda d: str(d["name"])),
        "stores": stores,
        "setups": [
            {"id": r.profile_id, "name": r.display_name}
            for r in db.execute(select(training_profiles).order_by(training_profiles.c.display_name))
        ],
        "voices": [
            {"id": r.voice_id, "name": r.display_name, "is_active": bool(r.is_active)}
            for r in db.execute(select(training_voices).order_by(training_voices.c.display_name))
        ],
        "completion_types": ["quiz", "walkthrough", "acknowledgment"],
        # Active employees' job titles (the Assignments page filter).
        "job_titles": [
            str(r.job_title)
            for r in db.execute(
                select(vw_trainees.c.job_title)
                .where(vw_trainees.c.is_active == 1, vw_trainees.c.job_title.is_not(None))
                .distinct()
                .order_by(vw_trainees.c.job_title)
            )
            if str(r.job_title).strip()
        ],
    }
