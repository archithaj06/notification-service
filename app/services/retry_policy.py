def should_retry(retries_used: int, max_retries: int) -> bool:
    """retries_used excludes the initial attempt -- e.g. after the 1st failure,
    retries_used == 0 (no retries consumed yet)."""
    return retries_used < max_retries


def compute_backoff_seconds(retries_used: int, base_delay_seconds: int) -> int:
    """
    Exponential backoff: base * 2^retries_used.
    With base=30s: retry 1 -> 30s, retry 2 -> 60s, retry 3 -> 120s.
    """
    return base_delay_seconds * (2 ** retries_used)
