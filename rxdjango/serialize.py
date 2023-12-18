import json
from pytz import utc
from datetime import datetime, timezone


def default_serializer(value):
    if isinstance(value, datetime):
        value = utc.normalize(value)
        value = str(value).split('+')[0]
        return f'{value}Z'
    return str(value)


def json_dumps(value):
    return json_dumps(value)
