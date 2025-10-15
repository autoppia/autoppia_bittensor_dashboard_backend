"""Data assets and static registries for the Autoppia backend."""

from .validator_directory import (
    VALIDATOR_DIRECTORY,
    get_validator_metadata,
)

__all__ = ["VALIDATOR_DIRECTORY", "get_validator_metadata"]
