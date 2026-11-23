"""Minimal polling agent for exercising the backend command protocol."""

import argparse
import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--api-key", default="change-me")
    parser.add_argument("--device-id", default="mock-device-1")
    parser.add_argument("--account-id", default="mock-account-1")
    args = parser.parse_args()
    headers = {"X-API-Key": args.api_key, "X-Tenant-ID": args.tenant_id}

    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=30) as client:
        while True:
            customer_text = await asyncio.to_thread(input, "\n客户消息> ")
            event_id = str(uuid4())
            event = {
                "device_id": args.device_id,
                "account_id": args.account_id,
                "external_event_id": event_id,
                "conversation_external_id": "mock-customer-1",
                "sender_external_id": "mock-customer-1",
                "message_type": "text",
                "content": customer_text,
                "occurred_at": datetime.now(UTC).isoformat(),
                "metadata": {"platform": "mock"},
            }
            response = await client.post("/api/v1/agents/events", json=event)
            response.raise_for_status()
            print("事件回执:", response.json())

            commands = await client.get(f"/api/v1/agents/{args.device_id}/commands")
            commands.raise_for_status()
            for command in commands.json():
                print("企微待发送:", command["payload"]["content"])
                ack = await client.post(
                    f"/api/v1/agents/{args.device_id}/commands/{command['id']}/ack",
                    json={"success": True, "external_message_id": f"mock-{uuid4()}"},
                )
                ack.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())

