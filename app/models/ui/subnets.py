from typing import List, Optional
from pydantic import BaseModel, Field, HttpUrl, field_validator


class MinerRosterEntry(BaseModel):
    """Metadata for a miner appearing in the animation roster."""

    miner_id: str = Field(..., description="Unique identifier used across timeline snapshots")
    display_name: str = Field(..., description="Display name used in UI elements")
    color_hex: str = Field(..., description="Primary color for line/label rendering")
    avatar_url: HttpUrl = Field(..., description="Avatar image URL suitable for square thumbnails")
    order: int = Field(..., ge=0, description="Stable ordering index for roster presentation")

    @field_validator("color_hex")
    @classmethod
    def validate_color_hex(cls, value: str) -> str:
        if not value.startswith("#") or len(value) != 7:
            raise ValueError("color_hex must be a 6-digit hex value prefixed with #")
        return value


class MinerSnapshot(BaseModel):
    """Per-round snapshot for an individual miner."""

    miner_id: str = Field(..., description="Identifier matching a roster entry")
    score: float = Field(..., ge=0.0, le=100.0, description="Performance score for the round (0-100)")
    rank: int = Field(..., ge=1, description="Ranking position at this round")
    rank_change: int = Field(..., description="Rank delta compared to previous round")
    score_change: float = Field(..., description="Score delta compared to previous round")
    previous_rank: Optional[int] = Field(
        None,
        description="Previous round rank if available"
    )


class TimelineRound(BaseModel):
    """Timeline entry describing a single round."""

    round: int = Field(..., ge=1, description="Sequential round number")
    timestamp: str = Field(..., description="Round timestamp in ISO 8601 format")
    snapshots: List[MinerSnapshot] = Field(
        ..., description="Snapshot for each miner in the roster"
    )


class TimelineMetaQuery(BaseModel):
    """Echoed query parameters for debugging."""

    rounds: Optional[int] = Field(None, description="Requested round count")
    end_round: Optional[int] = Field(None, description="Requested end round")
    seconds_back: Optional[int] = Field(None, description="Seconds back parameter")
    miners: Optional[int] = Field(None, description="Requested roster size")


class TimelineMeta(BaseModel):
    """Metadata associated with the generated timeline."""

    subnet_id: str = Field(..., description="Subnet identifier used to generate data")
    start_round: int = Field(..., ge=1, description="First round included in the timeline")
    end_round: int = Field(..., ge=1, description="Last round included in the timeline")
    round_count: int = Field(..., ge=1, description="Number of rounds returned")
    round_duration_seconds: int = Field(..., ge=1, description="Round duration used for timestamp spacing")
    generated_at: str = Field(..., description="ISO 8601 timestamp indicating generation time")
    query: TimelineMetaQuery = Field(..., description="Echoed request parameters")
    inferred_round_count: int = Field(..., ge=1, description="Derived round count before clamping")


class SubnetTimelineResponse(BaseModel):
    """Full response payload for the subnet timeline endpoint."""

    subnet_id: str = Field(..., description="Subnet identifier")
    roster: List[MinerRosterEntry] = Field(..., description="Miner roster metadata")
    timeline: List[TimelineRound] = Field(..., description="Ordered sequence of timeline rounds")
    meta: TimelineMeta = Field(..., description="Supplementary metadata for the response")
