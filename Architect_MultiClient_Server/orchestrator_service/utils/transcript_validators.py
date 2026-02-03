"""
Validation utilities for transcript API endpoints

Provides validation functions for all transcript-related data types.
"""

import re
from typing import Annotated, Optional
from fastapi import HTTPException, Query, Path

from orchestrator_service.config.transcript_config import VALIDATION_CONFIG as VC


# ============================================================================
# Validation Functions
# ============================================================================

def validate_object_id(value: str, field_name: str = "ID") -> str:
    """
    Validate MongoDB ObjectId format.
    
    Args:
        value: The ObjectId string to validate
        field_name: Name of the field for error messages
        
    Returns:
        The validated ObjectId string
        
    Raises:
        HTTPException: If the ObjectId format is invalid
    """
    if not re.match(VC.OBJECT_ID_PATTERN, value):
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid {field_name} format. Must be a valid 24-character hex string (MongoDB ObjectId)"
        )
    return value


def validate_room_name(value: str) -> str:
    """
    Validate room name format.
    
    Args:
        value: The room name to validate
        
    Returns:
        The validated room name
        
    Raises:
        HTTPException: If the room name is invalid
    """
    if not value or len(value) < VC.MIN_ROOM_NAME_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Room name must be at least {VC.MIN_ROOM_NAME_LENGTH} character(s)"
        )
    if len(value) > VC.MAX_ROOM_NAME_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Room name must not exceed {VC.MAX_ROOM_NAME_LENGTH} characters"
        )
    if not re.match(VC.ROOM_NAME_PATTERN, value):
        raise HTTPException(
            status_code=400,
            detail="Room name can only contain alphanumeric characters, underscores, hyphens, and dots"
        )
    return value


def validate_participant_identity(value: str) -> str:
    """
    Validate participant identity format.
    
    Args:
        value: The participant identity to validate
        
    Returns:
        The validated participant identity
        
    Raises:
        HTTPException: If the participant identity is invalid
    """
    if not value or len(value) < VC.MIN_PARTICIPANT_ID_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Participant identity must be at least {VC.MIN_PARTICIPANT_ID_LENGTH} character(s)"
        )
    if len(value) > VC.MAX_PARTICIPANT_ID_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Participant identity must not exceed {VC.MAX_PARTICIPANT_ID_LENGTH} characters"
        )
    return value


def validate_egress_id(value: str) -> str:
    """
    Validate egress ID format.
    
    Args:
        value: The egress ID to validate
        
    Returns:
        The validated egress ID
        
    Raises:
        HTTPException: If the egress ID is invalid
    """
    if not value or len(value) < VC.MIN_EGRESS_ID_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Egress ID must be at least {VC.MIN_EGRESS_ID_LENGTH} character(s)"
        )
    if len(value) > VC.MAX_EGRESS_ID_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Egress ID must not exceed {VC.MAX_EGRESS_ID_LENGTH} characters"
        )
    return value


def validate_date_range(start_date, end_date):
    """
    Validate that start_date is before end_date.
    
    Args:
        start_date: Start date
        end_date: End date
        
    Raises:
        HTTPException: If start_date >= end_date
    """
    if start_date >= end_date:
        raise HTTPException(
            status_code=400,
            detail="start_date must be before end_date"
        )


def validate_time_range(start_time: float, end_time: float):
    """
    Validate that start_time is before end_time.
    
    Args:
        start_time: Start time in seconds
        end_time: End time in seconds
        
    Raises:
        HTTPException: If start_time >= end_time
    """
    if start_time >= end_time:
        raise HTTPException(
            status_code=400,
            detail="start_time must be less than end_time"
        )


def validate_confidence_range(min_confidence: float, max_confidence: float):
    """
    Validate confidence range.
    
    Args:
        min_confidence: Minimum confidence value
        max_confidence: Maximum confidence value
        
    Raises:
        HTTPException: If min_confidence > max_confidence
    """
    if min_confidence > max_confidence:
        raise HTTPException(
            status_code=400,
            detail="min_confidence must be less than or equal to max_confidence"
        )


# ============================================================================
# Type Aliases for FastAPI Path/Query Parameters
# ============================================================================

RoomNamePath = Annotated[str, Path(
    min_length=VC.MIN_ROOM_NAME_LENGTH,
    max_length=VC.MAX_ROOM_NAME_LENGTH,
    pattern=VC.ROOM_NAME_PATTERN,
    description="Room name (alphanumeric, underscores, hyphens, dots only)"
)]

TrackIdPath = Annotated[str, Path(
    min_length=VC.MIN_TRACK_ID_LENGTH,
    max_length=VC.MAX_TRACK_ID_LENGTH,
    pattern=VC.OBJECT_ID_PATTERN,
    description="MongoDB ObjectId (24-character hex string)"
)]

EgressIdPath = Annotated[str, Path(
    min_length=VC.MIN_EGRESS_ID_LENGTH,
    max_length=VC.MAX_EGRESS_ID_LENGTH,
    description="Egress ID"
)]

ParticipantIdentityPath = Annotated[str, Path(
    min_length=VC.MIN_PARTICIPANT_ID_LENGTH,
    max_length=VC.MAX_PARTICIPANT_ID_LENGTH,
    description="Participant identity"
)]

ChunkIndexPath = Annotated[int, Path(
    ge=VC.MIN_CHUNK_INDEX,
    le=VC.MAX_CHUNK_INDEX,
    description=f"Chunk index ({VC.MIN_CHUNK_INDEX}-{VC.MAX_CHUNK_INDEX})"
)]

# Query parameters - DO NOT set default in Query(), use = in function signature instead
StatusQuery = Annotated[Optional[str], Query(
    min_length=VC.MIN_STATUS_LENGTH,
    max_length=VC.MAX_STATUS_LENGTH,
    description=f"Filter by status ({VC.MIN_STATUS_LENGTH}-{VC.MAX_STATUS_LENGTH} chars)"
)]

LimitQuery = Annotated[int, Query(
    ge=VC.MIN_LIMIT,
    le=VC.MAX_LIMIT,
    description=f"Maximum number of results ({VC.MIN_LIMIT}-{VC.MAX_LIMIT})"
)]

SkipQuery = Annotated[int, Query(
    ge=VC.MIN_SKIP,
    le=VC.MAX_SKIP,
    description=f"Number of records to skip ({VC.MIN_SKIP}-{VC.MAX_SKIP})"
)]

SearchQuery = Annotated[str, Query(
    min_length=VC.MIN_SEARCH_QUERY_LENGTH,
    max_length=VC.MAX_SEARCH_QUERY_LENGTH,
    description=f"Search text ({VC.MIN_SEARCH_QUERY_LENGTH}-{VC.MAX_SEARCH_QUERY_LENGTH} chars)"
)]
