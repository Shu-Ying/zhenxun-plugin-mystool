import asyncio
from importlib import import_module
from types import SimpleNamespace

from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException, RejectedException
import pytest
from tortoise import Tortoise


OWNER = "owner"
ALIAS = "alias"
FIRST_UID = "987654321"
SECOND_UID = "987654322"
GOODS_ID = "123"


@pytest.fixture
def account_commands(mystool):
    package = mystool.__name__
    return SimpleNamespace(
        package=package,
        storage=import_module(f"{package}.storage.session"),
        codec=import_module(f"{package}.storage.codec"),
        model=import_module(f"{package}.model.data"),
        common=import_module(f"{package}.model.common"),
        setting=import_module(f"{package}.command.setting").account_setting,
        address=import_module(f"{package}.command.address"),
        exchange=import_module(f"{package}.command.exchange").myb_exchange_plan,
    )


async def with_database(plugin, operation):
    model = plugin.model
    accounts = {
        FIRST_UID: model.UserAccount(
            cookies=model.BBSCookies(stuid=FIRST_UID), community_nickname="派蒙",
        ),
        SECOND_UID: model.UserAccount(
            cookies=model.BBSCookies(stuid=SECOND_UID), community_nickname="三月七",
        ),
    }
    plan = model.ExchangePlan(
        account=accounts[SECOND_UID],
        good={
            "goods_id": GOODS_ID, "name": "测试商品", "type": 1, "price": 100,
            "icon": "", "sale_start_time": 2_000_000_000, "next_time": 2_000_000_100,
            "account_exchange_num": 0, "account_cycle_limit": 1, "account_cycle_type": "forever",
        },
        scheduled_at=2_000_000_000,
    )
    user = model.UserData(accounts=accounts, exchange_plans=[plan])
    records = plugin.codec.encode(model.PluginData(users={OWNER: user}, user_bind={ALIAS: OWNER}))
    await Tortoise.init(
        db_url="sqlite://:memory:", modules={"models": [f"{plugin.package}.storage.models"]},
    )
    try:
        await Tortoise.generate_schemas()
        await plugin.storage.initialize_storage()
        repository = plugin.storage.Repository
        await repository.commit(await repository.load(), records)
        return await operation(repository)
    finally:
        await Tortoise.close_connections()


class CommandConversation:
    def __init__(self, command, monkeypatch):
        self.command = command
        self.matcher = command()
        self.event = SimpleNamespace(get_user_id=lambda: ALIAS)
        self.messages = []
        self.state = self.matcher.state
        for method in ("send", "finish", "reject"):
            monkeypatch.setattr(command, method, getattr(self, method))

    async def send(self, message):
        self.messages.append(str(message))

    async def finish(self, message):
        await self.send(message)
        raise FinishedException

    async def reject(self, message):
        await self.send(message)
        raise RejectedException

    def set_arg(self, key, value):
        self.matcher.set_arg(key, value)

    async def handle(self, number, **inputs):
        await self.command.handlers[number].call(
            event=self.event, matcher=self, state=self.state, **inputs,
        )


@pytest.mark.parametrize("command_name", ["setting", "address", "exchange"])
def test_account_number_selects_the_account_used_by_the_command(account_commands, monkeypatch, command_name):
    plugin = account_commands
    if command_name == "address":
        command = plugin.address.address_matcher
        address = plugin.common.Address(
            connect_areacode="+86", connect_mobile="10000000000", province_name="省",
            city_name="市", county_name="区", addr_ext="街道", connect_name="收件人", id="678",
        )

        async def get_address(account):
            assert account.bbs_uid == SECOND_UID
            return plugin.common.BaseApiStatus(success=True), [address]

        monkeypatch.setattr(plugin.address, "get_address", get_address)
    else:
        command = getattr(plugin, command_name)
    chat = CommandConversation(command, monkeypatch)

    async def run(repository):
        options = {"command": ("兑换", "-"), "command_arg": Message(GOODS_ID)} if command_name == "exchange" else {}
        await chat.handle(0, **options)
        menu = "\n".join(chat.messages)
        assert FIRST_UID not in menu and SECOND_UID not in menu
        assert any(line.startswith("1.") and "派蒙" in line for line in menu.splitlines())
        assert any(line.startswith("2.") and "三月七" in line for line in menu.splitlines())

        await chat.handle(1, account_choice="2")
        with pytest.raises(FinishedException):
            if command_name == "setting":
                await chat.handle(2, setting_id="1")
            elif command_name == "address":
                await chat.handle(2, address_id="678")
            else:
                await chat.handle(2, good_id=chat.matcher.get_arg("good_id").extract_plain_text())
        return await repository.load()

    records = asyncio.run(with_database(plugin, run))
    messages = "\n".join(chat.messages)
    assert FIRST_UID not in messages and SECOND_UID not in messages
    first = records["accounts"][(OWNER, FIRST_UID)]
    second = records["accounts"][(OWNER, SECOND_UID)]
    if command_name == "setting":
        assert first["enable_mission"] and not second["enable_mission"]
    elif command_name == "address":
        assert first["address"] is None and second["address"]["id"] == "678"
    else:
        assert not records["plans"]


def test_account_deletion_stops_when_the_shared_binding_has_been_removed(account_commands, monkeypatch):
    plugin = account_commands
    chat = CommandConversation(plugin.setting, monkeypatch)

    async def run(repository):
        await chat.handle(0)
        assert "派蒙" in "\n".join(chat.messages)
        await chat.handle(1, account_choice="1")
        with pytest.raises(RejectedException):
            await chat.handle(2, setting_id="8")
        assert "派蒙" in chat.messages[-1]
        await repository.update(lambda records: records["bindings"].pop(ALIAS))
        with pytest.raises(FinishedException):
            await chat.handle(2, setting_id="确认删除")
        return await repository.load()

    records = asyncio.run(with_database(plugin, run))
    assert (OWNER, FIRST_UID) in records["accounts"]
    assert "绑定" in chat.messages[-1]



def test_private_account_status_queries_families_without_mutating_records(account_commands, monkeypatch):
    plugin = account_commands
    module = import_module(f"{plugin.package}.command.account_status")
    chat = CommandConversation(module.account_status, monkeypatch)

    async def probe(account, family):
        assert account.bbs_uid == SECOND_UID
        return {"app": "可用", "web": "暂无法验证"}[family]

    monkeypatch.setattr(module, "_probe", probe)

    async def run(repository):
        before = await repository.load()
        await chat.handle(0)
        with pytest.raises(FinishedException):
            await chat.handle(1, account_choice="2")
        assert await repository.load() == before

    asyncio.run(with_database(plugin, run))
    message = chat.messages[-1]
    assert "三月七" in message and SECOND_UID not in message
    assert "App 凭据：可用" in message and "网页凭据：暂无法验证" in message


def test_role_selection_keeps_number_bound_to_region_and_uid(account_commands, monkeypatch):
    plugin = account_commands
    module = import_module(f"{plugin.package}.command.account_status")
    chat = CommandConversation(module.sign_roles, monkeypatch)
    first = plugin.common.GameRecord(game_id=2, game_role_id="111111111", region="cn_gf01",
                                     region_name="天空岛", nickname="旅行者", level=60)
    second = first.copy(update={"game_role_id": "222222222", "region": "cn_qd01", "region_name": "世界树"})
    calls = []

    async def roles(account):
        assert account.bbs_uid == SECOND_UID
        calls.append(True)
        return plugin.common.BaseApiStatus(success=True), [first, second] if len(calls) == 1 else [second, first]

    monkeypatch.setattr(module, "get_bound_game_roles", roles)

    async def run(repository):
        await chat.handle(0)
        await chat.handle(1, account_choice="2")
        await chat.handle(2, game_choice="1")
        with pytest.raises(FinishedException):
            await chat.handle(3, role_choice="2")
        return await repository.load()

    records = asyncio.run(with_database(plugin, run))
    assert records["accounts"][(OWNER, SECOND_UID)]["game_sign_roles"] == {"GenshinImpact": [["cn_qd01", "222222222"]]}
    assert all("111111111" not in message and "222222222" not in message for message in chat.messages)
