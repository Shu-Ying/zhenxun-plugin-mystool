from contextlib import asynccontextmanager
import asyncio
from contextvars import ContextVar
from functools import wraps

from nonebot.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from .codec import encode, decode
from .repository import Repository

_session = ContextVar("mystool_data_session", default=None)
_ready = asyncio.Event()


class DataSession:
    def __init__(self, records):
        self.owner = asyncio.current_task()
        self.base = records
        self.data = decode(records)

    async def save(self):
        try:
            pending = encode(self.data)
            if pending == self.base:
                return
            await Repository.commit(self.base, pending)
        except Exception as error:
            self.data = decode(self.base)
            logger.error(f"mystool 数据库事务未提交：{type(error).__name__}")
            raise
        self.base = pending


class ManagerMeta(type):
    @property
    def plugin_data(cls):
        session = _session.get()
        if session is None or session.owner is not asyncio.current_task():
            raise RuntimeError("业务数据必须在 data_session 中访问")
        return session.data


class PluginDataManager(metaclass=ManagerMeta):
    @classmethod
    @asynccontextmanager
    async def session(cls):
        existing = _session.get()
        if existing is not None and existing.owner is asyncio.current_task():
            yield existing
            return
        await _ready.wait()
        session = DataSession(await Repository.load())
        token = _session.set(session)
        try:
            yield session
        finally:
            _session.reset(token)

    @classmethod
    async def save(cls):
        session = _session.get()
        if session is None or session.owner is not asyncio.current_task():
            raise RuntimeError("数据库保存缺少业务上下文")
        await session.save()


def data_session(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        async with PluginDataManager.session() as session:
            result = await func(*args, **kwargs)
            await session.save()
            return result

    return wrapper


@PriorityLifecycle.on_startup(priority=2)
async def initialize_storage():
    await Repository.initialize()
    _ready.set()
    logger.info("mystool 数据库已就绪")
