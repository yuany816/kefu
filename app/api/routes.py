import hashlib
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.dependencies import (
    TenantId,
    get_knowledge_service,
    get_orchestrator,
    get_repository,
    get_settings_from_app,
    require_api_key,
)
from app.config import Settings
from app.domain.models import (
    AgentCommand,
    AgentEvent,
    CommandAck,
    ConversationOut,
    DocumentOut,
    EventReceipt,
    ReleaseRequest,
    SearchHit,
    SearchRequest,
    TakeoverRequest,
    TenantCreate,
    TenantOut,
    TextDocumentCreate,
)
from app.infra.repository import Repository
from app.services.documents import DocumentParser, UnsupportedDocumentType
from app.services.knowledge import KnowledgeService
from app.services.orchestrator import CustomerServiceOrchestrator


router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/dev/tenants",
    response_model=TenantOut,
    dependencies=[Depends(require_api_key)],
)
def create_tenant(
    payload: TenantCreate,
    repository: Annotated[Repository, Depends(get_repository)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
) -> TenantOut:
    if settings.app_env == "production":
        raise HTTPException(status_code=404, detail="not found")
    return TenantOut.model_validate(repository.create_tenant(payload.name))


@router.post("/knowledge/text", response_model=DocumentOut)
async def ingest_text(
    payload: TextDocumentCreate,
    tenant_id: TenantId,
    knowledge: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> DocumentOut:
    try:
        return await knowledge.ingest_text(tenant_id=tenant_id, document=payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/knowledge/files", response_model=DocumentOut)
async def ingest_file(
    tenant_id: TenantId,
    settings: Annotated[Settings, Depends(get_settings_from_app)],
    knowledge: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    file: UploadFile = File(...),
) -> DocumentOut:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="empty file")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file is larger than 25MB")
    filename = Path(file.filename or "upload.txt").name
    try:
        content = DocumentParser().parse(filename, data)
    except (UnsupportedDocumentType, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not content:
        raise HTTPException(status_code=422, detail="no text could be extracted from the file")

    digest = hashlib.sha256(data).hexdigest()
    object_name = f"{tenant_id}/{uuid4()}-{digest[:12]}{Path(filename).suffix.lower()}"
    object_path = settings.upload_dir / object_name
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(data)
    payload = TextDocumentCreate(
        title=filename,
        content=content,
        source_uri=f"upload://{object_name}",
        metadata={"original_filename": filename, "content_type": file.content_type},
    )
    return await knowledge.ingest_text(
        tenant_id=tenant_id,
        document=payload,
        source_type="upload",
        object_key=object_name,
    )


@router.post("/knowledge/search", response_model=list[SearchHit])
async def search_knowledge(
    payload: SearchRequest,
    tenant_id: TenantId,
    knowledge: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> list[SearchHit]:
    return await knowledge.search(tenant_id=tenant_id, query=payload.query, limit=payload.limit)


@router.post("/agents/events", response_model=EventReceipt)
async def receive_agent_event(
    event: AgentEvent,
    tenant_id: TenantId,
    orchestrator: Annotated[CustomerServiceOrchestrator, Depends(get_orchestrator)],
) -> EventReceipt:
    return await orchestrator.handle_event(tenant_id=tenant_id, event=event)


@router.get("/agents/{device_id}/commands", response_model=list[AgentCommand])
def poll_agent_commands(
    device_id: str,
    tenant_id: TenantId,
    repository: Annotated[Repository, Depends(get_repository)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
) -> list[AgentCommand]:
    repository.upsert_device(tenant_id, device_id)
    return repository.lease_commands(
        tenant_id=tenant_id,
        device_id=device_id,
        lease_seconds=settings.agent_command_lease_seconds,
    )


@router.post("/agents/{device_id}/commands/{command_id}/ack")
def ack_agent_command(
    device_id: str,
    command_id: UUID,
    payload: CommandAck,
    tenant_id: TenantId,
    repository: Annotated[Repository, Depends(get_repository)],
) -> dict[str, bool]:
    updated = repository.ack_command(
        tenant_id=tenant_id,
        device_id=device_id,
        command_id=command_id,
        success=payload.success,
        external_message_id=payload.external_message_id,
        error=payload.error,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="active command not found")
    return {"updated": True}


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: UUID,
    tenant_id: TenantId,
    repository: Annotated[Repository, Depends(get_repository)],
) -> ConversationOut:
    row = repository.get_conversation(tenant_id, conversation_id)
    if not row:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationOut.model_validate(row)


@router.post("/conversations/{conversation_id}/takeover", response_model=ConversationOut)
def take_over_conversation(
    conversation_id: UUID,
    payload: TakeoverRequest,
    tenant_id: TenantId,
    repository: Annotated[Repository, Depends(get_repository)],
) -> ConversationOut:
    try:
        row = repository.take_over(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            operator_id=payload.operator_id,
            reason=payload.reason,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ConversationOut.model_validate(row)


@router.post("/conversations/{conversation_id}/release", response_model=ConversationOut)
def release_conversation(
    conversation_id: UUID,
    payload: ReleaseRequest,
    tenant_id: TenantId,
    repository: Annotated[Repository, Depends(get_repository)],
) -> ConversationOut:
    try:
        row = repository.release_to_ai(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            operator_id=payload.operator_id,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ConversationOut.model_validate(row)

