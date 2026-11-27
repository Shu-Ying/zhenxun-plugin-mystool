import hashlib
import json
from collections import Counter


def plan_key(plan):
    return plan.legacy_plan_id or hashlib.sha256(
        json.dumps(plan.identity, ensure_ascii=False).encode()
    ).hexdigest()


def _restore_legacy_slot(plan, stored_id):
    next_time = plan.good.next_time
    if not next_time or next_time < 0:
        return
    sale_start = plan.good.sale_start_time
    # With an earlier opening, old saves could switch the hash to next_time
    # after that opening passed. Only the earlier slot's hash proves the slot.
    slot = sale_start if sale_start and 0 < sale_start < next_time else next_time
    if plan_key(plan.copy(update={"scheduled_at": slot})) == stored_id:
        plan.scheduled_at = slot


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
            exclude = {
                field for field in ("community_nickname", "web_scanned_at")
                if getattr(account, field) is None
            }
            records["accounts"][(user_id, uid)] = json.loads(account.json(exclude=exclude))
        for plan in user.exchange_plans:
            payload = json.loads(plan.json(exclude={"account", "legacy_plan_id"}))
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
    for user in users.values():
        names = Counter(account.display_name.casefold() for account in user.accounts.values())
        for account in user.accounts.values():
            if names[account.display_name.casefold()] > 1:
                account.disambiguate_display_name()
    for (uid, stored_id), value in records["plans"].items():
        payload = dict(value)
        account = users[uid].accounts[payload.pop("bbs_uid")]
        legacy = "scheduled_at" not in payload
        payload.setdefault("scheduled_at", None)
        plan = ExchangePlan.parse_obj({**payload, "account": account})
        plan.account = account
        if legacy:
            _restore_legacy_slot(plan, stored_id)
        if plan.scheduled_at is None:
            plan.legacy_plan_id = stored_id
            plan.paused_reason = "schedule_unconfirmed"
        users[uid].exchange_plans.add(plan)
    data = PluginData(users=users, user_bind=records["bindings"])
    data.do_user_bind()
    return data
