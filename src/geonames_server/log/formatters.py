import datetime as dt
import json
import logging

LOG_RECORD_BUILTIN_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class MyJSONFormatter(logging.Formatter):
    def __init__(self, fmt_keys=None):
        super().__init__()
        self.fmt_keys = fmt_keys or {}

    def format(self, record):
        message = {
            key: getattr(record, val, None) for key, val in self.fmt_keys.items()
        }

        message["message"] = record.getMessage()

        message["timestamp"] = dt.datetime.fromtimestamp(
            record.created, tz=dt.timezone.utc
        ).isoformat()

        if record.exc_info:
            message["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in LOG_RECORD_BUILTIN_ATTRS:
                message[key] = value

        return json.dumps(message)


class RichExtraFormatter(logging.Formatter):
    def format(self, record):
        message = record.getMessage()

        extras = {
            key: val
            for key, val in record.__dict__.items()
            if key not in LOG_RECORD_BUILTIN_ATTRS
        }

        if extras:
            message += " | " + " ".join(f"{k}={v}" for k, v in extras.items())

        return message
