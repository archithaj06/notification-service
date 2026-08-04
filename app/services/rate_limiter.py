import time

import redis

# Atomic sliding-window rate limit check using a Redis sorted set.
# Score = request timestamp (ms). On each check we:
#   1. Drop entries older than the window
#   2. Count what's left
#   3. If under the limit, record this request and allow it
#   4. If at/over the limit, reject without recording
# Running this as a single Lua script makes it atomic under concurrent
# requests for the same user -- a plain GET-then-SET from Python would have
# a race condition between two simultaneous requests.
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window_ms)
local count = redis.call('ZCARD', key)

if count >= limit then
    return {0, count}
end

redis.call('ZADD', key, now, now .. '-' .. math.random())
redis.call('PEXPIRE', key, window_ms)
return {1, count + 1}
"""


class RateLimitExceededError(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded. Retry after {retry_after_seconds}s")


class RateLimiter:
    def __init__(self, redis_client: redis.Redis, max_requests: int, window_seconds: int):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def check_and_record(self, user_id: str) -> None:
        """Raises RateLimitExceededError if the user is over their limit."""
        key = f"rate_limit:{user_id}"
        now_ms = int(time.time() * 1000)
        window_ms = self.window_seconds * 1000

        # Using EVAL directly (rather than SCRIPT LOAD + EVALSHA) trades a
        # small amount of network overhead for compatibility across Redis-like
        # backends (e.g. fakeredis in tests doesn't implement EVALSHA) --
        # acceptable given the script itself is tiny.
        allowed, _count = self.redis.eval(
            _SLIDING_WINDOW_SCRIPT, 1, key, now_ms, window_ms, self.max_requests
        )
        if not allowed:
            raise RateLimitExceededError(retry_after_seconds=self.window_seconds)
