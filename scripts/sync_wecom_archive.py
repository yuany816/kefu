"""Incrementally pull and ingest official WeCom conversation archives.

This worker must run on a platform supported by WeWorkFinanceSdk (normally
Linux in production). It reads/decrypts records and feeds customer-group
messages into the existing AI/handoff orchestrator.
"""

import argparse
import asyncio
from pathlib import Path
from uuid import UUID

from app.config import Settings
from app.infra.database import Database
from app.infra.repository import Repository
from app.integrations.wecom import archive_message_to_event
from app.integrations.wecom_finance import WeComFinanceSdk
from app.services.ai import build_ai_providers
from app.services.chunking import TextChunker
from app.services.knowledge import KnowledgeService
from app.services.orchestrator import CustomerServiceOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步企业微信会话内容存档")
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--once", action="store_true", help="拉取一次后退出")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=1000)
    return parser.parse_args()


def required_path(value: Path | None, name: str) -> Path:
    if value is None:
        raise SystemExit(f"缺少 {name}")
    return value


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    if not settings.wecom_archive_secret:
        raise SystemExit("缺少 WECOM_ARCHIVE_SECRET")

    database = Database(settings.database_url)
    database.open()
    repository = Repository(database)
    embedding_provider, chat_provider = build_ai_providers(settings)
    knowledge = KnowledgeService(
        repository=repository,
        embedding_provider=embedding_provider,
        chunker=TextChunker(),
    )
    orchestrator = CustomerServiceOrchestrator(
        repository=repository,
        knowledge=knowledge,
        chat_provider=chat_provider,
        settings=settings,
    )
    finance = WeComFinanceSdk(
        library_path=required_path(settings.wecom_finance_sdk_path, "WECOM_FINANCE_SDK_PATH"),
        corp_id=settings.wecom_corp_id,
        archive_secret=settings.wecom_archive_secret,
        private_key_path=required_path(
            settings.wecom_archive_private_key_path,
            "WECOM_ARCHIVE_PRIVATE_KEY_PATH",
        ),
    )
    try:
        while True:
            seq = repository.get_channel_sync_offset(
                args.tenant_id,
                channel="wecom_archive",
                account_id=settings.wecom_corp_id,
            )
            messages, max_seq = await asyncio.to_thread(
                finance.pull_decrypted,
                seq=seq,
                limit=args.limit,
            )
            accepted = duplicate = 0
            for message in messages:
                event = archive_message_to_event(
                    message,
                    corp_id=settings.wecom_corp_id,
                    internal_user_ids=set(),
                )
                if event is None:
                    continue
                receipt = await orchestrator.handle_event(
                    tenant_id=args.tenant_id,
                    event=event,
                )
                accepted += int(receipt.accepted)
                duplicate += int(receipt.duplicate)
            if max_seq > seq:
                repository.save_channel_sync_offset(
                    args.tenant_id,
                    channel="wecom_archive",
                    account_id=settings.wecom_corp_id,
                    last_seq=max_seq,
                )
            print(
                {
                    "from_seq": seq,
                    "to_seq": max_seq,
                    "pulled": len(messages),
                    "accepted": accepted,
                    "duplicate": duplicate,
                },
                flush=True,
            )
            if args.once:
                return
            await asyncio.sleep(args.poll_seconds)
    finally:
        finance.close()
        database.close()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
