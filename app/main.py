from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.infra.database import Database
from app.infra.repository import Repository
from app.integrations.wecom import WeComClient
from app.services.ai import build_ai_providers
from app.services.chunking import TextChunker
from app.services.knowledge import KnowledgeService
from app.services.orchestrator import CustomerServiceOrchestrator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    database = Database(settings.database_url)
    database.open()
    repository = Repository(database)
    embedding_provider, chat_provider = build_ai_providers(settings)
    knowledge_service = KnowledgeService(
        repository=repository,
        embedding_provider=embedding_provider,
        chunker=TextChunker(),
    )
    orchestrator = CustomerServiceOrchestrator(
        repository=repository,
        knowledge=knowledge_service,
        chat_provider=chat_provider,
        settings=settings,
    )
    wecom_client = WeComClient(
        corp_id=settings.wecom_corp_id,
        corp_secret=settings.wecom_corp_secret,
        base_url=settings.wecom_base_url,
        timeout_seconds=settings.wecom_request_timeout_seconds,
    )

    app.state.settings = settings
    app.state.database = database
    app.state.repository = repository
    app.state.knowledge_service = knowledge_service
    app.state.orchestrator = orchestrator
    app.state.wecom_client = wecom_client
    try:
        yield
    finally:
        await wecom_client.close()
        database.close()


app = FastAPI(
    title="Merchant AI Customer Service",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)

