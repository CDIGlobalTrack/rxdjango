import json
from pytz import utc
from datetime import datetime


def default_serializer(value):
    if isinstance(value, datetime):
        value = utc.normalize(value)
        value = str(value).split('+')[0]
        return f'{value}Z'
    return str(value)


def json_dumps(value):    
    return json.dumps(value, default=default_serializer)
