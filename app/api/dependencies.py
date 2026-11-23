from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings
from app.infra.repository import Repository
from app.services.knowledge import KnowledgeService
from app.services.orchestrator import CustomerServiceOrchestrator


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_repository(request: Request) -> Repository:
    return request.app.state.repository


def get_knowledge_service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


def get_orchestrator(request: Request) -> CustomerServiceOrchestrator:
    return request.app.state.orchestrator


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings_from_app)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    if not x_api_key or x_api_key != settings.dev_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")


def require_tenant(
    repository: Annotated[Repository, Depends(get_repository)],
    _: Annotated[None, Depends(require_api_key)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> UUID:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")
    try:
        tenant_id = UUID(x_tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid X-Tenant-ID") from exc
    if not repository.tenant_exists(tenant_id):
        raise HTTPException(status_code=404, detail="tenant not found")
    return tenant_id


TenantId = Annotated[UUID, Depends(require_tenant)]

