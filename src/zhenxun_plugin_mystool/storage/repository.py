import asyncio
from copy import deepcopy
from weakref import WeakKeyDictionary

from tortoise.transactions import in_transaction

from .codec import validate
from .models import (
    MystoolUser,
    MystoolAccount,
    MystoolPlan,
    MystoolBinding,
    MystoolRevision,
)


class ConcurrentUpdateError(RuntimeError):
    pass


def merge_concurrent_nickname(old, pending, actual):
    if old is None or actual is None:
        raise ConcurrentUpdateError("数据已被其他操作更新，请重新执行")
    baseline, current = old.copy(), actual.copy()
    old_nickname = baseline.pop("community_nickname", None)
    current_nickname = current.pop("community_nickname", None)
    if baseline != current or (
        pending is not None and pending.get("community_nickname") not in (old_nickname, current_nickname)
    ):
        raise ConcurrentUpdateError("数据已被其他操作更新，请重新执行")

    # A nickname backfill is independent of changes to account settings and deletion.
    if pending is None:
        return None
    merged = pending.copy()
    if current_nickname is None:
        merged.pop("community_nickname", None)
    else:
        merged["community_nickname"] = current_nickname
    return merged


class Repository:
    _locks = WeakKeyDictionary()

    @classmethod
    def lock(cls):
        loop = asyncio.get_running_loop()
        return cls._locks.setdefault(loop, asyncio.Lock())

    @staticmethod
    async def read(connection):
        records = {"users": {}, "accounts": {}, "plans": {}, "bindings": {}}
        for row in await MystoolUser.all().using_db(connection):
            records["users"][row.user_id] = {**row.settings, "uuid": row.uuid}
        for row in await MystoolAccount.all().using_db(connection):
            records["accounts"][(row.user_id, row.bbs_uid)] = row.data
        for row in await MystoolPlan.all().using_db(connection):
            records["plans"][(row.user_id, row.plan_id)] = row.data
        for row in await MystoolBinding.all().using_db(connection):
            records["bindings"][row.source] = row.target_id
        validate(records)
        return records

    @classmethod
    async def load(cls):
        async with in_transaction() as connection:
            await (
                MystoolRevision.filter(key="state")
                .using_db(connection)
                .select_for_update()
                .get()
            )
            return await cls.read(connection)

    @staticmethod
    def merge(base, pending, latest):
        merged = deepcopy(latest)
        for group in base:
            for key in base[group].keys() | pending[group].keys():
                old, new = base[group].get(key), pending[group].get(key)
                if old == new:
                    continue
                actual = latest[group].get(key)
                if actual != old and actual != new:
                    if group != "accounts":
                        raise ConcurrentUpdateError("数据已被其他操作更新，请重新执行")
                    new = merge_concurrent_nickname(old, new, actual)
                if key in pending[group]:
                    merged[group][key] = new
                else:
                    merged[group].pop(key, None)
        validate(merged)
        return merged

    @staticmethod
    async def apply(latest, merged, connection):
        models = {
            "users": MystoolUser,
            "accounts": MystoolAccount,
            "plans": MystoolPlan,
            "bindings": MystoolBinding,
        }

        def lookup(group, key):
            if group == "users":
                return {"user_id": key}
            if group == "bindings":
                return {"source": key}
            return {
                "user_id": key[0],
                "bbs_uid" if group == "accounts" else "plan_id": key[1],
            }

        for group in ("bindings", "plans", "accounts", "users"):
            for key in latest[group].keys() - merged[group].keys():
                await (
                    models[group]
                    .filter(**lookup(group, key))
                    .using_db(connection)
                    .delete()
                )
        for group in ("users", "accounts", "plans", "bindings"):
            for key, value in merged[group].items():
                if latest[group].get(key) == value:
                    continue
                if group == "users":
                    values = {
                        "uuid": value["uuid"],
                        "settings": {k: v for k, v in value.items() if k != "uuid"},
                    }
                elif group == "bindings":
                    values = {"target_id": value}
                else:
                    values = {"data": value}
                await models[group].update_or_create(
                    defaults=values, using_db=connection, **lookup(group, key)
                )

    @classmethod
    async def commit(cls, base, pending, *, check=None):
        def merge_latest(latest):
            if check is not None:
                check(latest)
            return cls.merge(base, pending, latest), None

        merged, _ = await cls._write(merge_latest)
        return merged

    @classmethod
    async def update(cls, mutate):
        """Verify and update the current records inside the repository transaction."""
        def update_records(latest):
            records = deepcopy(latest)
            result = mutate(records)
            validate(records)
            return records, result

        _, result = await cls._write(update_records)
        return result

    @classmethod
    async def _write(cls, update_records):
        async with cls.lock():
            async with in_transaction() as connection:
                revision = (
                    await MystoolRevision.filter(key="state")
                    .using_db(connection)
                    .select_for_update()
                    .get()
                )
                latest = await cls.read(connection)
                merged, result = update_records(latest)
                if merged == latest:
                    return merged, result
                changed = (
                    await MystoolRevision.filter(
                        key="state", revision=revision.revision
                    )
                    .using_db(connection)
                    .update(revision=revision.revision + 1)
                )
                if changed != 1:
                    raise ConcurrentUpdateError("数据库事务版本冲突，请重试")
                await cls.apply(latest, merged, connection)
        return merged, result

    @classmethod
    async def initialize(cls):
        async with in_transaction() as connection:
            await MystoolRevision.get_or_create(key="state", using_db=connection)
            await cls.read(connection)
