"""Sources health and refresh API."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from surebet.domain.enums import ACTIVE_COLLECTION_SOURCES, Source
from surebet.storage.snapshots import SnapshotRepository
from surebet.web.auth_middleware import require_authenticated_session, require_csrf_token

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/health")
def get_sources_health(
    request: Request,
    _auth: None = Depends(require_authenticated_session),
) -> dict[str, Any]:
    snapshot_repo: SnapshotRepository = request.app.state.snapshot_repo
    health_map = snapshot_repo.get_source_health()
    runner = getattr(request.app.state, "collector_runner", None)
    configured_sources = getattr(runner, "enabled_sources", ACTIVE_COLLECTION_SOURCES)
    enabled_sources = set(
        configured_sources if isinstance(configured_sources, tuple) else ACTIVE_COLLECTION_SOURCES
    )

    return {
        src.value: (
            {
                "source": src.value,
                "status": "disabled",
                "event_count": 0,
                "duration_ms": 0,
                "error_message": "Devre dışı bırakıldı (Veri toplanmıyor)",
                "last_success_at": None,
            }
            if src not in enabled_sources
            else (
                health_map[src].model_dump(mode="json")
                if src in health_map
                else {
                    "source": src.value,
                    "status": "unavailable",
                    "event_count": 0,
                    "duration_ms": 0,
                    "error_message": "Henüz veri toplanmadı",
                    "last_success_at": None,
                }
            )
        )
        for src in Source
    }


@router.post("/refresh")
async def refresh_sources(
    request: Request,
    _auth: None = Depends(require_authenticated_session),
    _csrf: None = Depends(require_csrf_token),
) -> dict[str, str]:
    runner = getattr(request.app.state, "collector_runner", None)
    if runner is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Collector runner not initialized",
        )

    triggered = await runner.trigger_refresh()
    if not triggered:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Lütfen yeni bir yenileme başlatmadan önce birkaç saniye bekleyin.",
        )

    return {"status": "triggered"}
