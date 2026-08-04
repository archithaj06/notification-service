import pytest

from app.services.retry_policy import compute_backoff_seconds, should_retry


@pytest.mark.parametrize(
    "retries_used,max_retries,expected",
    [
        (0, 3, True),
        (1, 3, True),
        (2, 3, True),
        (3, 3, False),
        (4, 3, False),
        (0, 0, False),
    ],
)
def test_should_retry(retries_used, max_retries, expected):
    assert should_retry(retries_used, max_retries) is expected


def test_compute_backoff_seconds_exponential_growth():
    base = 30
    assert compute_backoff_seconds(0, base) == 30
    assert compute_backoff_seconds(1, base) == 60
    assert compute_backoff_seconds(2, base) == 120
    assert compute_backoff_seconds(3, base) == 240


def test_compute_backoff_seconds_different_base():
    assert compute_backoff_seconds(0, 10) == 10
    assert compute_backoff_seconds(2, 10) == 40
