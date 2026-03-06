from datetime import datetime
from typing import Any

def convert_to_iso_8601(datetime_obj: datetime | Any) -> str:
    if isinstance(datetime_obj, datetime):
        return datetime_obj.replace(microsecond=0).isoformat() + 'Z'
    else:
        return str(datetime_obj)
