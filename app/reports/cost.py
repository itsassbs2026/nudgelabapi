"""Cost report (SPEC §7.2).

Totals and the Claude / Polly / Transcribe split, per session and per completion, the prompt-cache hit rate,
and breakdowns by day, training and setup. All figures are the agent's list-price estimates (session_usage),
not AWS bills.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.reference.agent_tables import session_usage, training_profiles, training_sessions, trainings
from app.reports import metrics
from app.reports.filters import ReportFilters, session_conditions


def cost_report(db: Session, f: ReportFilters) -> dict[str, Any]:
    s = training_sessions.c
    u = session_usage.c
    joined = training_sessions.join(session_usage, u.session_id == s.session_id).outerjoin(
        trainings, trainings.c.training_id == s.training_id
    )
    cond = and_(*session_conditions(f))
    tot = db.execute(
        select(
            func.count(),
            func.sum(u.est_llm_cost),
            func.sum(u.est_tts_cost),
            func.sum(u.est_stt_cost),
            func.sum(u.est_total_cost),
            func.sum(u.llm_input_tokens),
            func.sum(u.llm_cached_tokens),
        )
        .select_from(joined)
        .where(cond)
    ).one()
    sessions, llm, tts, stt, total, tokens_in, tokens_cached = tot
    total_f = metrics.rounded(total, 2) or 0.0
    completions = metrics.completions_in_period(db, f)

    def breakdown(column: Any, label_column: Any, label_join: Any) -> list[dict[str, Any]]:
        rows = db.execute(
            select(column, label_column, func.count(), func.sum(u.est_total_cost))
            .select_from(label_join)
            .where(cond)
            .group_by(column, label_column)
            .order_by(func.sum(u.est_total_cost).desc())
        ).all()
        return [
            {"id": r[0], "name": r[1] or r[0], "sessions": int(r[2]), "cost": metrics.rounded(r[3], 2)}
            for r in rows
        ]

    return {
        "sessions": int(sessions),
        "total": total_f,
        "split": {
            "claude": metrics.rounded(llm, 2) or 0.0,
            "polly": metrics.rounded(tts, 2) or 0.0,
            "transcribe": metrics.rounded(stt, 2) or 0.0,
        },
        "per_session": metrics.rounded(total_f / sessions, 3) if sessions else None,
        "completions": completions,
        "per_completion": metrics.rounded(total_f / completions, 2) if completions else None,
        "cache_hit_rate": metrics.ratio(int(tokens_cached or 0), int(tokens_in or 0)),
        "by_day": [{"date": d["date"], "cost": d["cost"]} for d in metrics.daily_series(db, f)],
        "by_training": breakdown(s.training_id, trainings.c.title, joined),
        "by_setup": breakdown(
            s.profile_id,
            training_profiles.c.display_name,
            joined.outerjoin(training_profiles, training_profiles.c.profile_id == s.profile_id),
        ),
        "note": "Estimates at list prices, from the agent's usage records.",
    }
