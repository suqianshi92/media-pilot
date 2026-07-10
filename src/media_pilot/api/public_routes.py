from fastapi import APIRouter

from media_pilot.api.schemas import ApiEnvelope

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def api_health() -> ApiEnvelope[dict[str, str]]:
    return ApiEnvelope(status="success", data={"version": "v1"})
