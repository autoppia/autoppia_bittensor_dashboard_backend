"""
Utility functions for formatting scores as percentages.
"""

from __future__ import annotations

# Tolerance for "score == 1.0" to avoid float equality (Sonar / reliability)
_SCORE_ONE_TOLERANCE = 1e-9


def format_score_as_percentage(score: float | int | None) -> str:
    """
    Convert a score from 0-1 format to 0-100 percentage format with % symbol.

    Args:
        score: Score in 0-1 format (or None)

    Returns:
        str: Formatted score as percentage (e.g., "87.5%")
    """
    if score is None:
        return "0%"

    # Convert to percentage and round to 1 decimal place
    percentage = round(float(score) * 100, 1)
    return f"{percentage}%"


def format_score_as_percentage_float(score: float | int | None) -> float:
    """
    Convert a score from 0-1 format to 0-100 percentage format as float.

    Args:
        score: Score in 0-1 format (or None)

    Returns:
        float: Score as percentage (e.g., 87.5)
    """
    if score is None:
        return 0.0

    score_float = float(score)

    # If score is already in percentage format (> 1.0), return as is
    # But be careful: 0.87 should become 87.0, not stay as 0.87
    if score_float > 1.0:
        return round(score_float, 1)

    # If score is effectively 1.0, treat as 100% (avoid float equality)
    if abs(score_float - 1.0) < _SCORE_ONE_TOLERANCE:
        return 100.0

    # Otherwise convert from 0-1 to percentage
    return round(score_float * 100, 1)


def format_score_round_data(score_round_data: list) -> list:
    """
    Convert score round data from 0-1 format to 0-100 percentage format.

    Args:
        score_round_data: List of score round data points

    Returns:
        list: Updated score round data with percentage scores
    """
    formatted_data = []
    for entry in score_round_data:
        formatted_entry = entry.copy()
        if "score" in formatted_entry:
            formatted_entry["score"] = format_score_as_percentage_float(formatted_entry["score"])
        formatted_data.append(formatted_entry)
    return formatted_data
