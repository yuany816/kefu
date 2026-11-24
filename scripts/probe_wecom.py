"""Read-only WeCom credential and customer-group scope probe."""

import asyncio

from app.config import Settings
from app.integrations.wecom import WeComAPIError, WeComClient


async def main() -> None:
    settings = Settings()
    client = WeComClient(
        corp_id=settings.wecom_corp_id,
        corp_secret=settings.wecom_corp_secret,
        base_url=settings.wecom_base_url,
        timeout_seconds=settings.wecom_request_timeout_seconds,
    )
    try:
        status = await client.probe()
        print(
            {
                "token_valid": status["token_valid"],
                "corp_id_suffix": status["corp_id"][-6:],
            }
        )
        try:
            groups = await client.get_customer_group_list(limit=1)
            print(
                {
                    "customer_group_scope": True,
                    "group_count_in_page": len(groups.get("group_chat_list", [])),
                    "has_more": bool(groups.get("next_cursor")),
                }
            )
        except WeComAPIError as exc:
            print(
                {
                    "customer_group_scope": False,
                    "errcode": exc.errcode,
                    "errmsg": exc.errmsg,
                }
            )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
