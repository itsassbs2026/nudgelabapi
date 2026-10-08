"""The Role Play report (app/reports/roleplay.py): practice conversations per track, and each debrief."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.agent_data import T, insert, seed

SEPT = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


def test_practice_scores_per_track(
    client: TestClient, trainer_headers: dict[str, str], db_session: Session
) -> None:
    seed(db_session)
    content = (Path(__file__).parent / "fixtures" / "content" / "win_every_customer.json").read_text(
        encoding="utf-8"
    )
    insert(db_session, "trainings", training_id="wec", title="Win Every Customer", status="active",
           completion_type="roleplay")  # fmt: skip
    version = {"version_id": 50, "training_id": "wec", "version_label": "v1", "content_hash": "c" * 64}
    insert(db_session, "training_versions", **version, content=content)
    db_session.execute(text("UPDATE trainings SET active_version_id = 50 WHERE training_id = 'wec'"))

    def attempt(uid: int, track: str, tier: str, score: int, day: int) -> None:
        debrief = {"strength": "Understood first", "gap": "Skipped the recap", "tip": "Recap the bill"}
        insert(db_session, "roleplay_attempts", uid=uid, training_id="wec", track=track, tier=tier,
               score=score, quick_pauses=int(score < 4), persona="Confused About the Bill", started_at=T(day),
               **debrief)  # fmt: skip

    attempt(1001, "billing", "beginner", 3, 5)
    attempt(1001, "billing", "beginner", 4, 6)  # opened the quiz
    attempt(1002, "billing", "beginner", 2, 7)
    attempt(1001, "billing", "stress", 2, 8)
    attempt(1003, "conduct", "beginner", 5, 9)
    report = client.get("/api/v1/roleplay", headers=trainer_headers, params=SEPT).json()
    tracks = {t["track"]: t for t in report["tracks"]}
    assert tracks["billing"] == {
        "training_id": "wec", "training_title": "Win Every Customer", "track": "billing", "practices": 3,
        "people": 2, "average_score": 3.0, "stress_practices": 1, "stress_average": 2.0, "opened_quiz": 1,
        "unlock_score": 4,
    }  # fmt: skip
    assert tracks["conduct"]["opened_quiz"] == 1
    assert report["total"] == 5 and report["items"][0]["track"] == "conduct"  # newest first
    assert report["items"][-1]["gap"] == "Skipped the recap" and report["items"][-1]["name"] == "Trainee 1001"
    stress = client.get("/api/v1/roleplay", headers=trainer_headers, params={**SEPT, "tier": "stress"}).json()
    assert stress["total"] == 1 and stress["items"][0]["score"] == 2
