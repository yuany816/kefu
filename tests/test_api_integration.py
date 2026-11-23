import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 with the pgvector container running",
)


def test_knowledge_to_agent_reply_and_handoff() -> None:
    with TestClient(app) as client:
        api_headers = {"X-API-Key": "change-me"}
        tenant_response = client.post(
            "/api/v1/dev/tenants",
            headers=api_headers,
            json={"name": f"集成测试商家-{uuid4()}"},
        )
        assert tenant_response.status_code == 200, tenant_response.text
        tenant_id = tenant_response.json()["id"]
        headers = {**api_headers, "X-Tenant-ID": tenant_id}

        document_response = client.post(
            "/api/v1/knowledge/text",
            headers=headers,
            json={
                "title": "退货政策",
                "content": "商品签收后7天内，不影响二次销售可申请退货。",
            },
        )
        assert document_response.status_code == 200, document_response.text
        assert document_response.json()["chunk_count"] == 1

        other_tenant_response = client.post(
            "/api/v1/dev/tenants",
            headers=api_headers,
            json={"name": f"隔离测试商家-{uuid4()}"},
        )
        assert other_tenant_response.status_code == 200, other_tenant_response.text
        other_headers = {
            **api_headers,
            "X-Tenant-ID": other_tenant_response.json()["id"],
        }
        isolated_search = client.post(
            "/api/v1/knowledge/search",
            headers=other_headers,
            json={"query": "签收后可以退货吗", "limit": 5},
        )
        assert isolated_search.status_code == 200, isolated_search.text
        assert isolated_search.json() == []

        search_response = client.post(
            "/api/v1/knowledge/search",
            headers=headers,
            json={"query": "签收后可以退货吗", "limit": 5},
        )
        assert search_response.status_code == 200, search_response.text
        assert search_response.json()
        assert "7天" in search_response.json()[0]["content"]

        event_id = f"event-{uuid4()}"
        event = {
            "device_id": "mock-device-1",
            "account_id": "wecom-account-1",
            "external_event_id": event_id,
            "conversation_external_id": "customer-001",
            "sender_external_id": "customer-001",
            "message_type": "text",
            "content": "签收后可以退货吗？",
            "occurred_at": datetime.now(UTC).isoformat(),
            "metadata": {"platform": "mock"},
        }
        receipt = client.post("/api/v1/agents/events", headers=headers, json=event)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["action"] == "reply_enqueued"
        conversation_id = receipt.json()["conversation_id"]

        commands = client.get(
            "/api/v1/agents/mock-device-1/commands",
            headers=headers,
        )
        assert commands.status_code == 200, commands.text
        reply_command = commands.json()[0]
        assert reply_command["payload"]["content"].startswith("【AI客服】")
        assert reply_command["payload"]["expected_last_event_id"] == event_id

        ack = client.post(
            f"/api/v1/agents/mock-device-1/commands/{reply_command['id']}/ack",
            headers=headers,
            json={"success": True, "external_message_id": f"sent-{uuid4()}"},
        )
        assert ack.status_code == 200, ack.text

        duplicate = client.post("/api/v1/agents/events", headers=headers, json=event)
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["duplicate"] is True

        event["external_event_id"] = f"event-{uuid4()}"
        event["content"] = "你回答得不对，请转人工"
        handoff = client.post("/api/v1/agents/events", headers=headers, json=event)
        assert handoff.status_code == 200, handoff.text
        assert handoff.json()["state"] == "human_pending"

        takeover = client.post(
            f"/api/v1/conversations/{conversation_id}/takeover",
            headers=headers,
            json={"operator_id": "operator-1", "reason": "customer_requested_human"},
        )
        assert takeover.status_code == 200, takeover.text
        assert takeover.json()["state"] == "human_active"

        cancelled_commands = client.get(
            "/api/v1/agents/mock-device-1/commands",
            headers=headers,
        )
        assert cancelled_commands.status_code == 200, cancelled_commands.text
        assert cancelled_commands.json() == []

        event["external_event_id"] = f"event-{uuid4()}"
        event["content"] = "还在吗？"
        human_owned = client.post("/api/v1/agents/events", headers=headers, json=event)
        assert human_owned.status_code == 200, human_owned.text
        assert human_owned.json()["action"] == "human_owns_conversation"

