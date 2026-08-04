import pytest

from app.services.rate_limiter import RateLimitExceededError, RateLimiter


def test_allows_requests_under_the_limit(fake_redis):
    limiter = RateLimiter(fake_redis, max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check_and_record("user-a")  # should not raise


def test_blocks_requests_over_the_limit(fake_redis):
    limiter = RateLimiter(fake_redis, max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check_and_record("user-a")

    with pytest.raises(RateLimitExceededError) as exc_info:
        limiter.check_and_record("user-a")
    assert exc_info.value.retry_after_seconds == 60


def test_users_are_rate_limited_independently(fake_redis):
    limiter = RateLimiter(fake_redis, max_requests=1, window_seconds=60)
    limiter.check_and_record("user-a")  # user-a now at limit

    limiter.check_and_record("user-b")  # different user, should not raise

    with pytest.raises(RateLimitExceededError):
        limiter.check_and_record("user-a")
