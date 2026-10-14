from tortoise import fields
from zhenxun.services.db_context import Model


class MystoolUser(Model):
    user_id = fields.CharField(255, pk=True, description="机器人用户ID")
    uuid = fields.CharField(36, unique=True, description="用户绑定密钥")
    settings = fields.JSONField(description="用户通知及验证设置")

    class Meta:
        table = "mystool_user"


class MystoolAccount(Model):
    user = fields.ForeignKeyField(
        "models.MystoolUser", related_name="accounts", on_delete=fields.CASCADE
    )
    bbs_uid = fields.CharField(255, description="米游社UID")
    data = fields.JSONField(description="Cookies、设备、地址和账号任务设置")

    class Meta:
        table = "mystool_account"
        unique_together = (("user", "bbs_uid"),)


class MystoolPlan(Model):
    user = fields.ForeignKeyField(
        "models.MystoolUser", related_name="plans", on_delete=fields.CASCADE
    )
    plan_id = fields.CharField(64, description="兑换计划稳定标识")
    data = fields.JSONField(description="商品、地址和游戏记录")

    class Meta:
        table = "mystool_plan"
        unique_together = (("user", "plan_id"),)


class MystoolBinding(Model):
    source = fields.CharField(255, pk=True, description="绑定发起用户ID")
    target = fields.ForeignKeyField(
        "models.MystoolUser", related_name="bindings", on_delete=fields.CASCADE
    )

    class Meta:
        table = "mystool_binding"


class MystoolRevision(Model):
    key = fields.CharField(32, pk=True, description="事务协调标识")
    revision = fields.BigIntField(default=0, description="提交版本")

    class Meta:
        table = "mystool_revision"
