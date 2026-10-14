from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER

from zhenxun.services.cache.runtime_cache import LevelUserMemoryCache


async def can_manage_accounts(bot: Bot, event: Event) -> bool:
    if await SUPERUSER(bot, event):
        return True
    global_user, _ = await LevelUserMemoryCache.get_levels(event.get_user_id(), None)
    return bool(global_user and global_user.user_level > 0)
