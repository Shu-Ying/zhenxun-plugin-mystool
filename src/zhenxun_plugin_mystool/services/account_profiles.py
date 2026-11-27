import asyncio

from tortoise.exceptions import BaseORMException

from ..api.profile import fetch_community_nickname
from ..logging import logger
from ..model.data import UserAccount, normalize_community_nickname
from ..storage.repository import ConcurrentUpdateError, Repository


def account_label(account: UserAccount) -> str:
    """Keep accounts with the same nickname distinguishable across menus."""
    return account.account_label


async def refresh_account_profiles(records: dict, actor: str) -> dict:
    """Backfill missing nicknames for the current owner and return current records."""
    owner = records["bindings"].get(actor, actor)
    owner_uuid = records["users"].get(owner, {}).get("uuid")
    candidates = {
        uid: row for (user_id, uid), row in records["accounts"].items()
        if user_id == owner and not normalize_community_nickname(row.get("community_nickname"), uid)
    }
    if not candidates:
        return records

    limit = asyncio.Semaphore(4)

    async def lookup(uid, row):
        try:
            account = UserAccount.parse_obj(row)
            async with limit:
                return await fetch_community_nickname(uid, account.device_id_android, account.cookies)
        except Exception as exc:
            logger.warning(f"米游社社区昵称查询失败：{type(exc).__name__}")
            return None

    names = await asyncio.gather(*(lookup(uid, row) for uid, row in candidates.items()))

    def save(current):
        current_owner = current["bindings"].get(actor, actor)
        if current_owner != owner or current["users"].get(owner, {}).get("uuid") != owner_uuid:
            return current

        for (uid, expected), nickname in zip(candidates.items(), names):
            if not nickname:
                continue
            row = current["accounts"].get((owner, uid))
            if (
                row is not None
                and not normalize_community_nickname(row.get("community_nickname"), uid)
                and row.get("cookies") == expected.get("cookies")
                and row.get("device_id_android") == expected.get("device_id_android")
                and row.get("credential_versions") == expected.get("credential_versions")
            ):
                row["community_nickname"] = nickname
        return current

    try:
        return await Repository.update(save)
    except (BaseORMException, ConcurrentUpdateError):
        logger.warning("米游社昵称回填保存失败")
        return records
