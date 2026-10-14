import sys

from zhenxun.services.log import logger as _host_logger

COMMAND = "米游社"


class PluginLogger:
    @staticmethod
    def info(message, **context):
        return _host_logger.info(message, COMMAND, **context)

    @staticmethod
    def debug(message, **context):
        return _host_logger.debug(message, COMMAND, **context)

    @staticmethod
    def warning(message, **context):
        return _host_logger.warning(message, COMMAND, **context)

    @staticmethod
    def error(message, **context):
        return _host_logger.error(message, COMMAND, **context)

    @staticmethod
    def success(message):
        return _host_logger.success(message, COMMAND)

    @staticmethod
    def exception(message, **context):
        return _host_logger.error(message, COMMAND, e=sys.exc_info()[1], **context)


logger = PluginLogger()
