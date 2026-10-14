from ..storage import data_session
from typing import Union
from uuid import uuid4

from nonebot import on_command
from nonebot.internal.matcher import Matcher
from nonebot.params import CommandArg, Command

from ..command.common import CommandRegistry
from ..model import PluginDataManager, plugin_config, uuid4_validate, CommandUsage
from ..utils import GeneralMessageEvent, COMMAND_BEGIN, get_last_command_sep, \
    GeneralGroupMessageEvent

__all__ = ["user_binding"]


user_binding = on_command(
    f"{plugin_config.preference.command_start}用户绑定",
    aliases={
        (f"{plugin_config.preference.command_start}用户绑定", "UUID"),
        (f"{plugin_config.preference.command_start}用户绑定", "uuid"),
        (f"{plugin_config.preference.command_start}用户绑定", "查询"),
        (f"{plugin_config.preference.command_start}用户绑定", "还原"),
        (f"{plugin_config.preference.command_start}用户绑定", "刷新UUID"),
        (f"{plugin_config.preference.command_start}用户绑定", "刷新uuid")
    },
    priority=5,
    block=True
)

CommandRegistry.set_usage(
    user_binding,
    CommandUsage(
        name="用户绑定",
        description="通过UUID绑定关联其他聊天平台或者其他账号的用户数据，以免去重新登录等操作",
        usage="具体用法：\n"
              "🔑 {HEAD}用户绑定{SEP}UUID ➢ 查看用于绑定的当前用户数据的UUID密钥\n"
              "🔍 {HEAD}用户绑定{SEP}查询 ➢ 查看当前用户的绑定情况\n"
              "↩️ {HEAD}用户绑定{SEP}还原 ➢ 清除当前用户的绑定关系，使当前用户数据成为空白数据\n"
              "🔄️ {HEAD}用户绑定{SEP}刷新UUID ➢ 重新生成当前用户的UUID密钥，同时原先与您绑定的用户将无法访问您当前的用户数据\n"
              "🖇️ {HEAD}用户绑定 <UUID> ➢ 绑定目标UUID的用户数据，当前用户的所有数据将被目标用户覆盖\n"
              "『{SEP}』为分隔符，使用NoneBot配置中的其他分隔符亦可"
    )
)
user_binding_usage = CommandRegistry.get_usage(user_binding)


@user_binding.handle()
@data_session
async def _(
        event: Union[GeneralMessageEvent],
        matcher: Matcher,
        command=Command(),
        command_arg=CommandArg()
):
    user_id = event.get_user_id()
    user = PluginDataManager.plugin_data.users.get(user_id)
    if len(command) > 1:
        if user is None:
            await matcher.finish("⚠️您的用户数据不存在，只有进行登录操作以后才会生成用户数据")
        elif command[1] in ["UUID", "uuid"]:
            if isinstance(event, GeneralGroupMessageEvent):
                await matcher.finish("⚠️为了保护您的隐私，请私聊进行UUID密钥查看。")

            await matcher.send(
                f"{'🔑您的UUID密钥为：' if user_id not in PluginDataManager.plugin_data.user_bind else '🔑您绑定的用户数据的UUID密钥为：'}\n"
                f"{user.uuid.upper()}\n"
                "可用于其他聊天平台进行数据绑定，请不要泄露给他人"
            )

        elif command[1] == "查询":
            if user_id in PluginDataManager.plugin_data.user_bind:
                await matcher.send(
                    "🖇️目前您绑定关联了用户：\n"
                    f"{PluginDataManager.plugin_data.user_bind[user_id]}\n"
                    "您的任何操作都将会影响到目标用户的数据"
                )
            elif user_id in PluginDataManager.plugin_data.user_bind.values():
                user_filter = filter(lambda x: PluginDataManager.plugin_data.user_bind[x] == user_id,
                                     PluginDataManager.plugin_data.user_bind)
                await matcher.send(
                    "🖇️目前有以下用户绑定了您的数据：\n"
                    "\n".join(user_filter)
                )
            else:
                await matcher.send("⚠️您当前没有绑定任何用户数据，也没有任何用户绑定您的数据")

        elif command[1] == "还原":
            if user_id not in PluginDataManager.plugin_data.user_bind:
                await matcher.finish("⚠️您当前没有绑定任何用户数据")
            else:
                del PluginDataManager.plugin_data.user_bind[user_id]
                del PluginDataManager.plugin_data.users[user_id]
                await PluginDataManager.save()
                await matcher.send("✔已清除当前用户的绑定关系，当前用户数据已是空白数据")

        elif command[1] in ["刷新UUID", "刷新uuid"]:
            if isinstance(event, GeneralGroupMessageEvent):
                await matcher.finish("⚠️为了保护您的隐私，请私聊进行UUID密钥刷新。")

            if user_id in PluginDataManager.plugin_data.user_bind:
                target_id = PluginDataManager.plugin_data.user_bind[user_id]
                be_bind = False
            else:
                target_id = user_id
                be_bind = True

            src_users = list(filter(lambda x: PluginDataManager.plugin_data.user_bind[x] == target_id,
                                    PluginDataManager.plugin_data.user_bind))
            for key in src_users:
                del PluginDataManager.plugin_data.user_bind[key]
                del PluginDataManager.plugin_data.users[key]
            PluginDataManager.plugin_data.users[target_id].uuid = str(uuid4())
            await PluginDataManager.save()

            await matcher.send(
                f"{'✔已刷新UUID密钥，原先绑定的用户将无法访问当前用户数据' if be_bind else '✔已刷新您绑定的用户数据的UUID密钥，目前您的用户数据已为空，您也可以再次绑定'}\n"
                f"🔑新的UUID密钥：{user.uuid.upper()}\n"
                "可用于其他聊天平台进行数据绑定，请不要泄露给他人"
            )
        else:
            await matcher.reject(
                '⚠️您的输入有误，二级命令不正确\n\n'
                f"{user_binding_usage.usage.format(HEAD=COMMAND_BEGIN, SEP=get_last_command_sep())}"
            )
    elif not command_arg:
        await matcher.send(
            f"『{COMMAND_BEGIN}{user_binding_usage.name}』- 使用说明\n"
            f"{user_binding_usage.description.format(HEAD=COMMAND_BEGIN)}\n"
            f"{user_binding_usage.usage.format(HEAD=COMMAND_BEGIN, SEP=get_last_command_sep())}"
        )
    else:
        uuid = str(command_arg).lower()
        if not uuid4_validate(uuid):
            await matcher.finish("⚠️您输入的UUID密钥格式不正确")
        elif user and uuid == user.uuid:
            await matcher.finish("⚠️您不能绑定自己的UUID密钥")
        else:
            # 筛选UUID密钥对应的用户
            target_users = list(
                filter(lambda x: x[1].uuid == uuid and x[0] != user_id, PluginDataManager.plugin_data.users.items()))
            # 如果有多个用户使用了此UUID密钥，即目标用户被多个用户绑定，需要进一步筛选，防止形成循环绑定的关系链
            if len(target_users) > 1:
                user_filter = filter(lambda x: x[0] not in PluginDataManager.plugin_data.user_bind, target_users)
                target_id, _ = next(user_filter)
            elif len(target_users) == 1:
                target_id, _ = target_users[0]
            else:
                await matcher.finish("⚠️找不到此UUID密钥对应的用户数据")
            PluginDataManager.plugin_data.do_user_bind(user_id, target_id)
            user = PluginDataManager.plugin_data.users[user_id]
            if isinstance(event, GeneralGroupMessageEvent):
                user.uuid = str(uuid4())
                await matcher.send("🔑由于您在群聊中进行绑定，已刷新您的UUID密钥，但不会影响其他已绑定用户")
            await PluginDataManager.save()
            await matcher.send(f"✔已绑定用户 {target_id} 的用户数据")
