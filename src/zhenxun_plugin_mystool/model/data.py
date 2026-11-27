import re
from hashlib import sha256
from typing import Union, Optional, Any, Dict, TYPE_CHECKING, AbstractSet, \
    Mapping, Set, Literal, List
from unicodedata import category
from uuid import UUID, uuid4

from httpx import Cookies
from ..logging import logger
from pydantic.v1 import BaseModel, PrivateAttr, validator

from .._version import __version__
from ..model.common import BaseModelWithSetter, Address, BaseModelWithUpdate, Good, GameRecord

if TYPE_CHECKING:
    IntStr = Union[int, str]
    DictStrAny = Dict[str, Any]
    AbstractSetIntStr = AbstractSet[IntStr]
    MappingIntStrAny = Mapping[IntStr, Any]

__all__ = ["BBSCookies", "UserAccount", "ExchangePlan", "ExchangeResult", "uuid4_validate",
           "UserData", "PluginData", "PluginDataManager"]



class BBSCookies(BaseModelWithSetter, BaseModelWithUpdate):
    """
    米游社Cookies数据

    # 测试 is_correct() 方法

    >>> assert BBSCookies().is_correct() is False
    >>> assert BBSCookies(stuid="123", stoken="123", cookie_token="123").is_correct() is True

    # 测试 bbs_uid getter

    >>> bbs_cookies = BBSCookies()
    >>> assert not bbs_cookies.bbs_uid
    >>> assert BBSCookies(stuid="123").bbs_uid == "123"

    # 测试 bbs_uid setter

    >>> bbs_cookies.bbs_uid = "123"
    >>> assert bbs_cookies.bbs_uid == "123"

    # 检查构造函数内所用的 stoken setter

    >>> bbs_cookies = BBSCookies(stoken="abcd1234")
    >>> assert bbs_cookies.stoken_v1 and not bbs_cookies.stoken_v2
    >>> bbs_cookies = BBSCookies(stoken="v2_abcd1234==")
    >>> assert bbs_cookies.stoken_v2 and not bbs_cookies.stoken_v1
    >>> assert bbs_cookies.stoken == "v2_abcd1234=="

    # 检查 stoken setter

    >>> bbs_cookies = BBSCookies(stoken="abcd1234")
    >>> bbs_cookies.stoken = "v2_abcd1234=="
    >>> assert bbs_cookies.stoken_v2 == "v2_abcd1234=="
    >>> assert bbs_cookies.stoken_v1 == "abcd1234"

    # 检查 .dict 方法能否生成包含 stoken_2 类型的 stoken 的字典

    >>> bbs_cookies = BBSCookies()
    >>> bbs_cookies.stoken_v1 = "abcd1234"
    >>> bbs_cookies.stoken_v2 = "v2_abcd1234=="
    >>> assert bbs_cookies.dict(v2_stoken=True)["stoken"] == "v2_abcd1234=="

    # 检查是否有多余的字段

    >>> bbs_cookies = BBSCookies(stuid="123")
    >>> assert all(bbs_cookies.dict())
    >>> assert all(map(lambda x: x not in bbs_cookies, ["stoken_v1", "stoken_v2"]))

    # 测试 update 方法

    >>> bbs_cookies = BBSCookies(stuid="123")
    >>> assert bbs_cookies.update({"stuid": "456", "stoken": "abc"}) is bbs_cookies
    >>> assert bbs_cookies.stuid == "456"
    >>> assert bbs_cookies.stoken == "abc"

    >>> bbs_cookies = BBSCookies(stuid="123")
    >>> new_cookies = BBSCookies(stuid="456", stoken="abc")
    >>> assert bbs_cookies.update(new_cookies) is bbs_cookies
    >>> assert bbs_cookies.stuid == "456"
    >>> assert bbs_cookies.stoken == "abc"
    """
    stuid: Optional[str]
    """米游社UID"""
    ltuid: Optional[str]
    """米游社UID"""
    account_id: Optional[str]
    """米游社UID"""
    login_uid: Optional[str]
    """米游社UID"""

    stoken_v1: Optional[str]
    """保存stoken_v1，方便后续使用"""
    stoken_v2: Optional[str]
    """保存stoken_v2，方便后续使用"""

    cookie_token: Optional[str]
    login_ticket: Optional[str]
    ltoken: Optional[str]
    mid: Optional[str]
    ltoken_v2: Optional[str] = None
    cookie_token_v2: Optional[str] = None
    ltuid_v2: Optional[str] = None
    account_id_v2: Optional[str] = None
    ltmid_v2: Optional[str] = None
    account_mid_v2: Optional[str] = None

    def __init__(self, **data: Any):
        super().__init__(**data)
        stoken = data.get("stoken")
        if stoken:
            self.stoken = stoken

    def is_correct(self) -> bool:
        """判断是否为正确的Cookies"""
        return self.has_credentials("app") and self.has_credentials("web")

    def has_credentials(self, family: str) -> bool:
        if family == "app":
            return bool(self.bbs_uid and (self.stoken_v2 and self.mid or self.stoken_v1))
        if family == "web":
            return bool(
                self.bbs_uid and (
                    self.ltoken_v2 and self.cookie_token_v2 and self.ltmid_v2 and self.account_mid_v2
                    or self.ltoken or self.cookie_token
                )
            )
        if family == "combined":
            return self.has_credentials("app") or self.has_credentials("web")
        raise ValueError("未知的凭据类型")

    def request_cookies(self, family: str) -> Dict[str, str]:
        uid = self.bbs_uid
        if family == "app":
            if self.stoken_v2 and self.mid and uid:
                return {"stuid": uid, "stoken": self.stoken_v2, "mid": self.mid}
            if self.stoken_v1 and uid:
                return {"stuid": uid, "stoken": self.stoken_v1}
            return {}
        if family == "web":
            if uid and self.ltoken_v2 and self.cookie_token_v2 and self.ltmid_v2 and self.account_mid_v2:
                return {
                    "ltoken_v2": self.ltoken_v2, "cookie_token_v2": self.cookie_token_v2,
                    "ltuid_v2": self.ltuid_v2 or uid, "account_id_v2": self.account_id_v2 or uid,
                    "ltmid_v2": self.ltmid_v2, "account_mid_v2": self.account_mid_v2,
                }
            legacy = {
                "ltuid": uid, "account_id": uid, "ltoken": self.ltoken, "cookie_token": self.cookie_token,
            }
            return {key: value for key, value in legacy.items() if value}
        if family == "combined":
            return self.request_cookies("app") | self.request_cookies("web")
        raise ValueError("未知的凭据类型")

    @property
    def bbs_uid(self):
        """
        获取米游社UID
        """
        uid = None
        for value in [self.stuid, self.ltuid, self.account_id, self.login_uid, self.ltuid_v2, self.account_id_v2]:
            if value:
                uid = value
                break
        return uid or None

    @bbs_uid.setter
    def bbs_uid(self, value: str):
        self.stuid = value
        self.ltuid = value
        self.account_id = value
        self.login_uid = value

    @property
    def stoken(self):
        """
        获取stoken
        :return: 优先返回 self.stoken_v1
        """
        if self.stoken_v1:
            return self.stoken_v1
        elif self.stoken_v2:
            return self.stoken_v2
        else:
            return None

    @stoken.setter
    def stoken(self, value):
        if value.startswith("v2_"):
            self.stoken_v2 = value
        else:
            self.stoken_v1 = value

    def update(self, cookies: Union[Dict[str, str], Cookies, "BBSCookies"]):
        """
        更新Cookies
        """
        if not isinstance(cookies, BBSCookies):
            self.stoken = cookies.get("stoken") or self.stoken
            self.bbs_uid = cookies.get("bbs_uid") or self.bbs_uid
            cookies.pop("stoken", None)
            cookies.pop("bbs_uid", None)
        return super().update(cookies)

    def dict(self, *,
             include: Optional[Union['AbstractSetIntStr', 'MappingIntStrAny']] = None,
             exclude: Optional[Union['AbstractSetIntStr', 'MappingIntStrAny']] = None,
             by_alias: bool = False,
             skip_defaults: Optional[bool] = None, exclude_unset: bool = False, exclude_defaults: bool = False,
             exclude_none: bool = False, v2_stoken: bool = False,
             cookie_type: bool = False) -> 'DictStrAny':
        """
        获取Cookies字典

        v2_stoken: stoken 字段是否使用 stoken_v2
        cookie_type: 是否返回符合Cookie类型的字典（没有自定义的stoken_v1、stoken_v2键）
        """
        # 保证 stuid, ltuid 等字段存在
        self.bbs_uid = self.bbs_uid
        cookies_dict = super().dict(include=include, exclude=exclude, by_alias=by_alias, skip_defaults=skip_defaults,
                                    exclude_unset=exclude_unset, exclude_defaults=exclude_defaults,
                                    exclude_none=exclude_none)
        if v2_stoken and self.stoken_v2:
            cookies_dict["stoken"] = self.stoken_v2
        else:
            cookies_dict["stoken"] = self.stoken_v1

        if cookie_type:
            # 去除自定义的 stoken_v1, stoken_v2 字段
            cookies_dict.pop("stoken_v1")
            cookies_dict.pop("stoken_v2")

            # 去除空的字段
            empty_key = set()
            for key, value in cookies_dict.items():
                if not value:
                    empty_key.add(key)
            [cookies_dict.pop(key) for key in empty_key]

        return cookies_dict


def normalize_community_nickname(nickname: object, uid: Optional[str] = None) -> Optional[str]:
    if not isinstance(nickname, str):
        return None
    nickname = "".join(
        char for char in nickname
        if char.isspace() or char in "\u200c\u200d" or category(char) not in {"Cc", "Cf", "Cs"}
    )
    nickname = " ".join(nickname.split())
    if not nickname or (uid and uid in nickname):
        return None
    return nickname


class UserAccount(BaseModelWithSetter):
    """
    米游社账户数据

    >>> user_account = UserAccount(
    >>>     cookies=BBSCookies(),
    >>>     device_id_ios="DBB8886C-C88A-4E12-A407-BE295E95E084",
    >>>     device_id_android="64561CE4-5F43-41D7-B92F-41CEFABC7ABF"
    >>> )
    >>> assert isinstance(user_account, UserAccount)
    >>> user_account.bbs_uid = "123"
    >>> assert user_account.bbs_uid == "123"
    """
    phone_number: Optional[str]
    """手机号"""
    community_nickname: Optional[str] = None
    web_scanned_at: Optional[int] = None
    """最近一次确认 Web 扫码的 Unix 时间戳。"""
    """米游社昵称"""
    cookies: BBSCookies
    """Cookies"""
    address: Optional[Address]
    """收货地址"""

    device_id_ios: str
    """iOS设备用 deviceID"""
    device_id_android: str
    """安卓设备用 deviceID"""
    device_fp: Optional[str]
    """iOS设备用 deviceFp"""
    enable_mission: bool = True
    '''是否开启米游币任务计划'''
    enable_game_sign: bool = True
    '''是否开启米游社游戏签到计划'''
    enable_resin: bool = True
    '''是否开启便笺提醒'''
    platform: Literal["ios", "android"] = "ios"
    '''设备平台'''
    game_sign_games: List[str] = [
        "GenshinImpact",
        "HonkaiImpact3",
        "HoukaiGakuen2",
        "TearsOfThemis",
        "StarRail",
        "ZenlessZoneZero"
    ]
    '''允许签到的游戏列表'''
    game_sign_roles: Dict[str, List[tuple[str, str]]] = {}
    mission_games: List[str] = ["BBSMission"]
    '''在哪些板块执行米游币任务计划 为 BaseMission 子类名称'''
    user_stamina_threshold: int = 240
    '''崩铁便笺体力提醒阈值，0为一直提醒'''
    user_resin_threshold: int = 200
    '''原神便笺树脂提醒阈值，0为一直提醒'''
    credential_versions: Dict[str, int] = {}
    expired_credentials: Dict[str, str] = {}
    credential_expiry_notices: Dict[str, List[str]] = {}
    _show_account_label: bool = PrivateAttr(False)

    def __init__(self, **data: Any):
        if not data.get("device_id_ios") or not data.get("device_id_android"):
            from ..utils import generate_device_id
            if not data.get("device_id_ios"):
                data.setdefault("device_id_ios", generate_device_id())
            if not data.get("device_id_android"):
                data.setdefault("device_id_android", generate_device_id())

        super().__init__(**data)

    @validator("community_nickname", pre=True)
    def validate_community_nickname(cls, value):
        return normalize_community_nickname(value)

    @property
    def bbs_uid(self):
        """
        获取米游社UID
        """
        return self.cookies.bbs_uid

    @bbs_uid.setter
    def bbs_uid(self, value: str):
        self.cookies.bbs_uid = value

    @property
    def account_label(self) -> str:
        identity = f"{self.device_id_android}:{self.bbs_uid}"
        # Use letters so the suffix cannot be mistaken for an account UID.
        suffix = sha256(identity.encode()).hexdigest()[:8].translate(
            str.maketrans("0123456789abcdef", "ABCDEFGHIJKLMNOP")
        )
        return f"账号 {suffix}"

    def disambiguate_display_name(self) -> None:
        self._show_account_label = True

    @property
    def display_name(self) -> str:
        nickname = normalize_community_nickname(self.community_nickname, self.bbs_uid)
        name = re.sub(
            r"\[CQ:[^\[\]]*\]?",
            lambda match: match[0].replace("[", "［").replace("]", "］"),
            nickname or "昵称待更新",
        )
        return f"{name}（{self.account_label}）" if self._show_account_label else name


class ExchangePlan(BaseModel):
    """
    兑换计划数据类
    """

    good: Good
    """商品"""
    address: Optional[Address]
    """地址ID"""
    account: UserAccount
    """米游社账号"""
    game_record: Optional[GameRecord]
    """商品对应的游戏的玩家账号"""
    scheduled_at: Optional[int] = None
    """创建计划时选定的兑换场次时间戳"""
    paused_reason: Optional[Literal["schedule_unconfirmed", "schedule_passed", "target_unconfirmed"]] = None
    generation: Optional[str] = None
    legacy_plan_id: Optional[str] = None
    """无法恢复原场次时，沿用数据库中原有的计划 ID"""

    def __init__(self, **data: Any):
        super().__init__(**data)
        if "scheduled_at" not in data:
            self.scheduled_at = self.good.time

    @property
    def redemption_target(self):
        return (
            self.good.goods_id,
            self.address.id if self.address else None,
            self.account.bbs_uid,
            self.game_record.game_role_id if self.game_record else None,
        )

    @property
    def identity(self):
        goods_id, address_id, account_id, role_id = self.redemption_target
        slot = self.legacy_plan_id or self.scheduled_at
        if self.generation is not None and self.good.is_virtual and self.game_record is not None:
            return goods_id, slot, account_id, self.game_record.game_id, self.game_record.region, role_id
        return goods_id, slot, address_id, account_id, role_id

    def __hash__(self):
        return hash(self.identity)

    def __eq__(self, other):
        if not isinstance(other, ExchangePlan):
            return NotImplemented
        return self.identity == other.identity

    class CustomDict(dict):
        _hash: int

        def __hash__(self):
            return self._hash

    def dict(
            self,
            *,
            include: Optional[Union['AbstractSetIntStr', 'MappingIntStrAny']] = None,
            exclude: Optional[Union['AbstractSetIntStr', 'MappingIntStrAny']] = None,
            by_alias: bool = False,
            skip_defaults: Optional[bool] = None,
            exclude_unset: bool = False,
            exclude_defaults: bool = False,
            exclude_none: bool = False,
    ) -> 'DictStrAny':
        """
        重写 dict 方法，使其返回的 dict 可以被 hash
        """
        normal_dict = super().dict(include=include, exclude=exclude, by_alias=by_alias, skip_defaults=skip_defaults,
                                   exclude_unset=exclude_unset, exclude_defaults=exclude_defaults,
                                   exclude_none=exclude_none)
        hashable_dict = ExchangePlan.CustomDict(normal_dict)
        hashable_dict._hash = hash(self)
        return hashable_dict


class ExchangeResult(BaseModel):
    """
    兑换结果数据类
    """
    result: bool
    """兑换结果"""
    return_data: dict
    """返回数据"""
    plan: ExchangePlan
    """兑换计划"""


def uuid4_validate(v):
    """
    验证UUID是否为合法的UUIDv4

    :param v: UUID
    """
    try:
        UUID(v, version=4)
    except Exception:
        return False
    else:
        return True


class UserData(BaseModelWithSetter):
    """
    用户数据类

    >>> userdata = UserData()
    >>> hash(userdata)
    """
    enable_notice: bool = True
    """是否开启通知"""
    geetest_url: Optional[str]
    '''极验Geetest人机验证打码接口URL'''
    geetest_params: Optional[Dict[str, Any]] = None
    '''极验Geetest人机验证打码API发送的参数（除gt，challenge外）'''
    uuid: Optional[str] = None
    """用户UUID密钥，用于不同NoneBot适配器平台之间的数据同步，因此不可泄露"""
    exchange_plans: Union[Set[ExchangePlan], List[ExchangePlan]] = set()
    """兑换计划列表"""
    accounts: Dict[str, UserAccount] = {}
    """储存一些已绑定的账号数据"""

    @validator("uuid")
    def uuid_validator(cls, v):
        """
        验证UUID是否为合法的UUIDv4

        :raises ValueError: UUID格式错误，不是合法的UUIDv4
        """
        if v is not None and not uuid4_validate(v):
            raise ValueError("UUID格式错误，不是合法的UUIDv4")
        return v

    def __init__(self, **data: Any):
        super().__init__(**data)

        exchange_plans = self.exchange_plans
        self.exchange_plans = set()
        for plan in exchange_plans:
            plan = ExchangePlan.parse_obj(plan)
            self.exchange_plans.add(plan)

        if self.uuid is None:
            self.uuid = str(uuid4())

    def __hash__(self):
        return hash(self.uuid)


class PluginData(BaseModel):
    version: str = __version__
    """创建插件数据文件时的版本号"""
    user_bind: Optional[Dict[str, str]] = {}
    '''不同NoneBot适配器平台的用户数据绑定关系（如QQ聊天和QQ频道）(空用户数据:被绑定用户数据)'''
    users: Dict[str, UserData] = {}
    '''所有用户数据'''

    def do_user_bind(self, src: str = None, dst: str = None):
        """
        执行用户数据绑定同步，将src指向dst的用户数据，即src处的数据将会被dst处的数据对象替换

        :param src: 源用户数据，为空则读取 self.user_bind 并执行全部绑定
        :param dst: 目标用户数据，为空则读取 self.user_bind 并执行全部绑定
        """
        if None in [src, dst]:
            for x, y in self.user_bind.items():
                try:
                    self.users[x] = self.users[y]
                except KeyError:
                    logger.error(f"用户数据绑定失败，目标用户 {y} 不存在")
        else:
            try:
                self.user_bind[src] = dst
                self.users[src] = self.users[dst]
            except KeyError:
                logger.error(f"用户数据绑定失败，目标用户 {dst} 不存在")

    def __init__(self, **data: Any):
        super().__init__(**data)
        self.do_user_bind()

    class Config:
        json_encoders = UserAccount.Config.json_encoders


from ..storage import PluginDataManager
