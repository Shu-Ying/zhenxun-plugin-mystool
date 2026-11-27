from ..model.common import GameRecord
from ..model.data import normalize_community_nickname


def role_key(record: GameRecord) -> tuple[str, str]:
    return record.region, record.game_role_id


def role_label(record: GameRecord) -> str:
    name = normalize_community_nickname(record.nickname, record.game_role_id) or "游戏角色"
    region = normalize_community_nickname(record.region_name) or "未知区服"
    return f"{name}（{region} · Lv.{record.level}）".replace("[", "［").replace("]", "］")


def select_game_roles(account, game, records):
    candidates = [record for record in records if record.game_id == game.game_id]
    saved = account.game_sign_roles.get(game.en_name)
    if saved is None:
        return candidates if len(candidates) <= 1 else None
    by_key = {role_key(record): record for record in candidates}
    if any(key not in by_key for key in saved):
        return None
    return [by_key[key] for key in saved]
