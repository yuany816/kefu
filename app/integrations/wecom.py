from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic
from typing import Any

import httpx

from app.domain.models import AgentEvent, MessageType


class WeComAPIError(RuntimeError):
    def __init__(self, errcode: int, errmsg: str) -> None:
        super().__init__(f"企业微信 API 错误 {errcode}: {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg


class WeComNotConfiguredError(RuntimeError):
    pass


class WeComClient:
    """Small async client for the official WeCom server API.

    Access tokens are cached in memory only. Secrets and tokens are never logged
    or returned from application endpoints.
    """

    TOKEN_ERROR_CODES = {40001, 40014, 42001}

    def __init__(
        self,
        *,
        corp_id: str,
        corp_secret: str,
        base_url: str = "https://qyapi.weixin.qq.com",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.corp_id = corp_id
        self._corp_secret = corp_secret
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.corp_id and self._corp_secret)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not self.configured:
            raise WeComNotConfiguredError("未配置 WECOM_CORP_ID/WECOM_CORP_SECRET")
        if not force_refresh and self._token and monotonic() < self._token_expires_at:
            return self._token

        async with self._token_lock:
            if not force_refresh and self._token and monotonic() < self._token_expires_at:
                return self._token
            response = await self._client.get(
                "/cgi-bin/gettoken",
                params={"corpid": self.corp_id, "corpsecret": self._corp_secret},
            )
            response.raise_for_status()
            payload = response.json()
            self._raise_for_api_error(payload)
            self._token = str(payload["access_token"])
            expires_in = max(int(payload.get("expires_in", 7200)) - 120, 60)
            self._token_expires_at = monotonic() + expires_in
            return self._token

    async def probe(self) -> dict[str, Any]:
        await self.get_access_token(force_refresh=True)
        return {"configured": True, "token_valid": True, "corp_id": self.corp_id}

    async def get_customer_group_list(
        self,
        *,
        status_filter: int = 0,
        owner_filter: list[str] | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "status_filter": status_filter,
            "limit": limit,
        }
        if owner_filter:
            body["owner_filter"] = {"userid_list": owner_filter}
        if cursor:
            body["cursor"] = cursor
        return await self._api_request(
            "POST", "/cgi-bin/externalcontact/groupchat/list", json=body
        )

    async def get_customer_group(self, chat_id: str, *, need_name: bool = True) -> dict[str, Any]:
        return await self._api_request(
            "POST",
            "/cgi-bin/externalcontact/groupchat/get",
            json={"chat_id": chat_id, "need_name": 1 if need_name else 0},
        )

    async def _api_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any],
        retry_token: bool = True,
    ) -> dict[str, Any]:
        token = await self.get_access_token()
        response = await self._client.request(
            method,
            path,
            params={"access_token": token},
            json=json,
        )
        response.raise_for_status()
        payload = response.json()
        errcode = int(payload.get("errcode", 0))
        if retry_token and errcode in self.TOKEN_ERROR_CODES:
            await self.get_access_token(force_refresh=True)
            return await self._api_request(method, path, json=json, retry_token=False)
        self._raise_for_api_error(payload)
        return payload

    @staticmethod
    def _raise_for_api_error(payload: dict[str, Any]) -> None:
        errcode = int(payload.get("errcode", 0))
        if errcode:
            raise WeComAPIError(errcode, str(payload.get("errmsg", "unknown error")))


def archive_message_to_event(
    message: dict[str, Any],
    *,
    corp_id: str,
    internal_user_ids: set[str],
) -> AgentEvent | None:
    """Map one decrypted Finance SDK record to the internal channel event.

    Only external-user messages in customer groups are accepted. Messages sent
    by enterprise members are ignored to prevent the AI from replying to itself.
    """

    msg_id = str(message.get("msgid", ""))
    room_id = str(message.get("roomid", ""))
    sender = str(message.get("from", ""))
    msg_type = str(message.get("msgtype", ""))
    if (
        not msg_id
        or not room_id
        or not sender
        or message.get("action", "send") != "send"
        or sender in internal_user_ids
        or not sender.startswith(("wm", "wo"))
    ):
        return None

    message_type = MessageType.SYSTEM
    content = ""
    if msg_type == "text":
        message_type = MessageType.TEXT
        content = str((message.get("text") or {}).get("content", ""))
    elif msg_type == "image":
        message_type = MessageType.IMAGE
        content = "[图片]"
    elif msg_type == "voice":
        message_type = MessageType.VOICE
        content = "[语音]"
    elif msg_type == "file":
        message_type = MessageType.FILE
        filename = str((message.get("file") or {}).get("filename", ""))
        content = f"[文件]{filename}" if filename else "[文件]"
    else:
        content = f"[暂不支持的消息类型:{msg_type or 'unknown'}]"

    msg_time = int(message.get("msgtime") or 0)
    occurred_at = (
        datetime.fromtimestamp(msg_time / 1000, tz=UTC)
        if msg_time > 0
        else datetime.now(UTC)
    )
    return AgentEvent(
        device_id=f"wecom-api:{corp_id}",
        account_id=corp_id,
        external_event_id=msg_id,
        conversation_external_id=room_id,
        sender_external_id=sender,
        message_type=message_type,
        content=content,
        occurred_at=occurred_at,
        metadata={
            "platform": "wecom_api",
            "channel": "wecom_customer_group",
            "room_id": room_id,
            "wecom_message_type": msg_type,
            "archive_action": message.get("action"),
        },
    )
