import hashlib
import json


def plan_key(plan):
    identity = [
        plan.good.goods_id,
        plan.good.time,
        plan.address.id if plan.address else None,
        plan.account.bbs_uid,
        plan.game_record.game_role_id if plan.game_record else None,
    ]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def encode(data):
    records = {
        "users": {},
        "accounts": {},
        "plans": {},
        "bindings": dict(data.user_bind),
    }
    for user_id, user in data.users.items():
        if user_id in data.user_bind:
            continue
        records["users"][user_id] = json.loads(
            user.json(exclude={"accounts", "exchange_plans"})
        )
        for uid, account in user.accounts.items():
            records["accounts"][(user_id, uid)] = json.loads(account.json())
        for plan in user.exchange_plans:
            payload = json.loads(plan.json(exclude={"account"}))
            payload["bbs_uid"] = plan.account.bbs_uid
            records["plans"][(user_id, plan_key(plan))] = payload
    validate(records)
    return records


def validate(records):
    owners = records["users"]
    for source, target in records["bindings"].items():
        if source == target or source in owners or target not in owners:
            raise ValueError("无效的用户绑定关系")
    for user_id, _ in records["accounts"]:
        if user_id not in owners:
            raise ValueError("账号缺少所属用户")
    for (user_id, _), plan in records["plans"].items():
        if (user_id, plan["bbs_uid"]) not in records["accounts"]:
            raise ValueError("兑换计划引用了不存在的账号")


def decode(records):
    from ..model.data import PluginData, UserData, UserAccount, ExchangePlan

    users = {uid: UserData.parse_obj(value) for uid, value in records["users"].items()}
    for (uid, bbs_uid), value in records["accounts"].items():
        users[uid].accounts[bbs_uid] = UserAccount.parse_obj(value)
    for (uid, _), value in records["plans"].items():
        payload = dict(value)
        account = users[uid].accounts[payload.pop("bbs_uid")]
        plan = ExchangePlan.parse_obj({**payload, "account": account})
        plan.account = account
        users[uid].exchange_plans.add(plan)
    data = PluginData(users=users, user_bind=records["bindings"])
    data.do_user_bind()
    return data
