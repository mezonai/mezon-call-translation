import re
from typing import Dict
from datetime import datetime


class FilepathParser:
    """Parser for filepath from egress"""

    PATTERN = re.compile(
        r"""
        ^(?P<room>[^/]+)/                              # room
        (?P<identity>[^-]+)-                           # identity
        (?P<source>[^-]+)-                             # source
        (?P<track_type>audio|video)-                   # track type
        (?P<timestamp>\d{8}_\d{6})                     # timestamp
        \.(?P<ext>ogg|webm)$                           # extension
        """,
        re.VERBOSE
    )

    @classmethod
    def parse(cls, filepath: str) -> Dict[str, str]:
        """
        Parse filepath into components and convert timestamp to ISO format

        Raises:
            ValueError: If filepath does not match format
        """
        match = cls.PATTERN.match(filepath)
        if not match:
            raise ValueError(f"Invalid filepath format: {filepath}")
        
        result = match.groupdict()
        
        # Convert timestamp from "YYYYmmdd_HHMMSS" to ISO format "YYYY-MM-DDTHH:MM:SS"
        try:
            timestamp_str = result.get("timestamp", "")
            dt = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            result["timestamp_iso"] = dt.isoformat()
        except (ValueError, AttributeError):
            result["timestamp_iso"] = None
        
        return result