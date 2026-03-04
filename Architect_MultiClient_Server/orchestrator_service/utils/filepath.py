import re
import random
from typing import Dict
from datetime import datetime


class Filepath:
    """Parser for filepath from egress"""

    PATTERN = re.compile(
        r"""
        ^(?P<room_id>[^/]+)/                           # room_id
        (?P<identity>.+?)[-_]{1,2}                     # identity (flexible separator: - or __ or _)
        (?P<source>[^-]+)-                             # source
        (?P<track_type>audio|video)-                   # track type
        (?P<random_id>\d{6})                           # 6 random digits
        \.(?P<ext>ogg|webm)$                           # extension
        """,
        re.VERBOSE
    )
    @classmethod
    def build(cls, identity: str, source: str, 
                       track_type: str, room_id: str) -> str:
        """Create filepath for MinIO storage with random suffix"""
        ext = "ogg" if track_type == "AUDIO" else "webm"
        random_id = random.randint(100000, 999999)  # 6 random digits
        
        return f"{room_id}/{identity}-{source}-{track_type.lower()}-{random_id}.{ext}"


    @classmethod
    def parse(cls, filepath: str) -> Dict[str, str]:
        """
        Parse filepath into components using regex.

        Raises:
            ValueError: If filepath does not match format
        """
        match = cls.PATTERN.match(filepath)
        if not match:
            raise ValueError(f"Invalid filepath format: {filepath}")
        
        result = match.groupdict()
        return result