"""Exports (SPEC §8.2, Phase 5). CSV comes straight back; XLSX is queued for the worker.

`POST /exports` with `format: "csv"` answers 200 with the file; with `format: "xlsx"` it answers 202 with a
job. Poll `GET /exports/{id}` until `done`, then fetch `GET /exports/{id}/download`. Every export is audit
logged.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser, get_user
from app.config import Settings, get_settings
from app.db import get_db
from app.exports import service
from app.exports.writer import csv_chunks
from app.routers.auth import client_ip
from app.schemas.reports import ExportJob

router = APIRouter(tags=["exports"])

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/exports", response_model=None, responses={202: {"model": ExportJob}})
def create_export(
    body: service.ExportRequest,
    request: Request,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    report, f, params = service.resolve(body, timezone=current.user.timezone, is_admin=current.is_admin)
    if body.format == "xlsx":
        job = service.queue_xlsx(db, current, body, ip=client_ip(request))
        payload = ExportJob.model_validate(service.job_out(job)).model_dump(mode="json")
        return JSONResponse(payload, status_code=202)
    rows = service.fetch_rows(db, settings, report, f, params)
    service.record_export(db, current, body, ip=client_ip(request), rows=len(rows), job_id=None)
    db.commit()
    return StreamingResponse(
        csv_chunks(report, rows, current.user.timezone),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{service.filename(report, f, "csv")}"'},
    )


@router.get("/exports/{job_id}", response_model=ExportJob)
def get_export(job_id: int, current: CurrentUser = Depends(get_user), db: Session = Depends(get_db)) -> Any:
    return service.job_out(service.own_export(db, current, job_id))


@router.get("/exports/{job_id}/download", response_class=FileResponse)
def download_export(
    job_id: int,
    current: CurrentUser = Depends(get_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    path, name = service.export_file(settings, service.own_export(db, current, job_id))
    return FileResponse(path, media_type=XLSX_TYPE, filename=name)
