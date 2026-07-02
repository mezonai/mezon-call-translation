from datetime import UTC, datetime

TimeValue = datetime | str | int | float

def convert_to_iso_8601(datetime_obj: TimeValue) -> str:
    if isinstance(datetime_obj, datetime):
        if datetime_obj.tzinfo is not None:
            datetime_obj = datetime_obj.astimezone(UTC).replace(tzinfo=None)
        return datetime_obj.replace(microsecond=0).isoformat() + "Z"
    else:
        return str(datetime_obj)
