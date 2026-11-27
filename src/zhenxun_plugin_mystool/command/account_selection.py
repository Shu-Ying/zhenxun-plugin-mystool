from collections import Counter
from dataclasses import dataclass

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from ..model.data import UserAccount, normalize_community_nickname
from ..services.account_profiles import account_label, refresh_account_profiles
from ..storage import PluginDataManager
from ..storage.repository import Repository
from ..utils import COMMAND_BEGIN


class InvalidAccountChoice(ValueError):
    pass


class StaleAccountChoice(ValueError):
    pass


ACCOUNT_CHANGED = "账号已删除或绑定关系发生变化，请重新执行命令。"


@dataclass(frozen=True)
class AccountOption:
    uid: str
    device: str
    label: str


class AccountMenu:
    """The numbered options and ownership captured when the bot displays the list."""

    def __init__(self, owner: str, owner_uuid: str | None, accounts):
        self.owner = owner
        self.owner_uuid = owner_uuid
        self.has_missing_nickname = any(
            not normalize_community_nickname(account.community_nickname, uid)
            for uid, account in accounts.items()
        )
        names = {uid: account.display_name for uid, account in accounts.items()}
        counts = Counter(name.casefold() for name in names.values())
        self.options = {}
        for number, uid in enumerate(sorted(names), start=1):
            account = accounts[uid]
            name = names[uid]
            if counts[name.casefold()] > 1:
                name = f"{name}（{account_label(account)}）"
            self.options[str(number)] = AccountOption(uid, account.device_id_android, name)

    def __len__(self):
        return len(self.options)

    @classmethod
    def from_data(cls, data, actor: str):
        owner = data.user_bind.get(actor, actor)
        user = data.users.get(owner)
        return cls(owner, user.uuid if user else None, user.accounts if user else {})

    @classmethod
    def from_records(cls, records, actor: str):
        owner = records["bindings"].get(actor, actor)
        accounts = {
            uid: UserAccount.parse_obj(account)
            for (account_owner, uid), account in records["accounts"].items()
            if account_owner == owner
        }
        return cls(owner, records["users"].get(owner, {}).get("uuid"), accounts)

    def prompt(self, title: str, *, allow_new: bool = False) -> Message:
        lines = [title]
        lines.extend(f"{number}. {option.label}" for number, option in self.options.items())
        if allow_new:
            lines.append("0. 新增账号")
        lines.append("请回复序号，或回复「取消」。")
        return Message(MessageSegment.text("\n".join(lines)))

    def label_for(self, uid: str) -> str:
        return next(option.label for option in self.options.values() if option.uid == uid)

    def same_owner(self, current) -> bool:
        return (self.owner, self.owner_uuid) == (current.owner, current.owner_uuid)

    def has_same_account(self, uid: str, current) -> bool:
        if not self.same_owner(current):
            return False
        old = next((option for option in self.options.values() if option.uid == uid), None)
        new = next((option for option in current.options.values() if option.uid == uid), None)
        return old is not None and new is not None and old.device == new.device

    def pick(self, choice: str, current, *, allow_new: bool = False) -> str | None:
        def identities(menu):
            return [(option.uid, option.device) for option in menu.options.values()]

        if not self.same_owner(current) or identities(self) != identities(current):
            raise StaleAccountChoice
        if allow_new and choice.strip() in {"0", "新增"}:
            return None
        option = self.options.get(choice.strip())
        if option is None:
            raise InvalidAccountChoice
        return option.uid


async def ask_for_account(matcher, state, event, title: str):
    actor = event.get_user_id()
    menu = AccountMenu.from_data(PluginDataManager.plugin_data, actor)
    if menu.has_missing_nickname:
        records = await refresh_account_profiles(await Repository.load(), actor)
        menu = AccountMenu.from_records(records, actor)
    if not menu:
        await matcher.finish(f"你尚未绑定米游社账号，请私聊发送「{COMMAND_BEGIN}登录」。")
    state["account_menu"] = menu
    if len(menu) == 1:
        matcher.set_arg("account_choice", Message("1"))
    else:
        await matcher.send(menu.prompt(title))


async def select_account(matcher, state, event, choice: str, title: str) -> UserAccount:
    if choice.strip() in {"取消", "退出"}:
        await matcher.finish("已取消选择账号。")
    data = PluginDataManager.plugin_data
    current = AccountMenu.from_data(data, event.get_user_id())
    if not current:
        await matcher.finish(f"当前没有可选账号，请私聊发送「{COMMAND_BEGIN}登录」。")
    menu = state.get("account_menu")
    try:
        if menu is None:
            raise StaleAccountChoice
        uid = menu.pick(choice, current)
    except StaleAccountChoice:
        state["account_menu"] = current
        await matcher.reject(current.prompt(f"账号列表已变化。{title}"))
    except InvalidAccountChoice:
        await matcher.reject(menu.prompt(title))
    state["bbs_uid"] = uid
    return data.users[current.owner].accounts[uid]


async def require_selected_account(matcher, state, event) -> UserAccount:
    data = PluginDataManager.plugin_data
    current = AccountMenu.from_data(data, event.get_user_id())
    menu = state.get("account_menu")
    uid = state.get("bbs_uid")
    if menu is None or not menu.has_same_account(uid, current):
        await matcher.finish(ACCOUNT_CHANGED)
    return data.users[current.owner].accounts[uid]


async def save_selected_account(matcher, state, event):
    menu = state.get("account_menu")
    actor = event.get_user_id()
    uid = state.get("bbs_uid")

    def check(records):
        if menu is None or not menu.has_same_account(uid, AccountMenu.from_records(records, actor)):
            raise StaleAccountChoice

    try:
        await PluginDataManager.save(check=check)
    except StaleAccountChoice:
        await matcher.finish(ACCOUNT_CHANGED)
