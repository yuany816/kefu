from datetime import UTC

import httpx
import pytest

from app.domain.models import MessageType
from app.integrations.wecom import WeComAPIError, WeComClient, archive_message_to_event


@pytest.mark.asyncio
async def test_client_caches_token_and_lists_groups() -> None:
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/cgi-bin/gettoken":
            token_calls += 1
            assert request.url.params["corpid"] == "corp-1"
            return httpx.Response(
                200,
                json={"errcode": 0, "access_token": "token-1", "expires_in": 7200},
            )
        assert request.url.path == "/cgi-bin/externalcontact/groupchat/list"
        assert request.url.params["access_token"] == "token-1"
        return httpx.Response(
            200,
            json={"errcode": 0, "errmsg": "ok", "group_chat_list": []},
        )

    client = WeComClient(
        corp_id="corp-1",
        corp_secret="secret-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        first = await client.get_customer_group_list(limit=20)
        second = await client.get_customer_group_list(limit=20)
    finally:
        await client.close()

    assert first["group_chat_list"] == []
    assert second["errcode"] == 0
    assert token_calls == 1


@pytest.mark.asyncio
async def test_client_surfaces_api_error_without_leaking_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200,
                json={"errcode": 40013, "errmsg": "invalid corpid"},
            )
        raise AssertionError("unexpected request")

    client = WeComClient(
        corp_id="bad-corp",
        corp_secret="do-not-leak",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(WeComAPIError) as exc_info:
            await client.probe()
    finally:
        await client.close()

    assert exc_info.value.errcode == 40013
    assert "do-not-leak" not in str(exc_info.value)


def test_archive_group_text_maps_to_event() -> None:
    event = archive_message_to_event(
        {
            "msgid": "message-1",
            "action": "send",
            "from": "wm-external-user-1",
            "roomid": "room-1",
            "msgtime": 1795471200000,
            "msgtype": "text",
            "text": {"content": "营业时间是什么时候？"},
        },
        corp_id="corp-1",
        internal_user_ids={"employee-1"},
    )

    assert event is not None
    assert event.message_type == MessageType.TEXT
    assert event.conversation_external_id == "room-1"
    assert event.content == "营业时间是什么时候？"
    assert event.occurred_at.tzinfo == UTC


def test_archive_ignores_enterprise_member_message() -> None:
    event = archive_message_to_event(
        {
            "msgid": "message-2",
            "from": "employee-1",
            "roomid": "room-1",
            "msgtype": "text",
            "text": {"content": "人工已经接入"},
        },
        corp_id="corp-1",
        internal_user_ids={"employee-1"},
    )

    assert event is None


def test_archive_ignores_recalled_and_unknown_sender_messages() -> None:
    base = {
        "msgid": "message-3",
        "from": "wm-external-user-1",
        "roomid": "room-1",
        "msgtype": "text",
        "text": {"content": "已经撤回"},
    }
    assert archive_message_to_event(
        {**base, "action": "recall"},
        corp_id="corp-1",
        internal_user_ids=set(),
    ) is None
    assert archive_message_to_event(
        {**base, "action": "send", "from": "employee-without-prefix"},
        corp_id="corp-1",
        internal_user_ids=set(),
    ) is None
