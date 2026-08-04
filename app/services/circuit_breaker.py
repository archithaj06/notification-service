import time

import redis

# Per-channel circuit breaker backed by Redis so state is shared across all
# worker processes (an in-memory breaker would only protect the one process
# that happens to see the failures, and RQ workers are meant to be scaled
# horizontally). Classic three-state machine:
#   closed     -- calls go through normally; consecutive failures are counted.
#   open       -- calls are rejected immediately (no provider call made) until
#                 recovery_seconds has elapsed since the circuit opened.
#   half_open  -- exactly one probe call is let through to test the provider;
#                 success closes the circuit, failure reopens it.
# All state transitions happen inside a single Lua script per call so two
# workers racing on the same channel can't both slip through as the "one"
# half-open probe, or double-decrement past open.
_ALLOW_REQUEST_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local recovery_seconds = tonumber(ARGV[2])

local state = redis.call('HGET', key, 'state')
if state == false then
    state = 'closed'
end

if state == 'closed' then
    return 1
end

if state == 'open' then
    local opened_at = tonumber(redis.call('HGET', key, 'opened_at')) or 0
    if now - opened_at >= recovery_seconds then
        redis.call('HSET', key, 'state', 'half_open', 'probe_taken', '1')
        redis.call('EXPIRE', key, recovery_seconds * 10)
        return 1
    end
    return 0
end

-- half_open: let exactly one probe through
local probe_taken = redis.call('HGET', key, 'probe_taken')
if probe_taken == '0' or probe_taken == false then
    redis.call('HSET', key, 'probe_taken', '1')
    return 1
end
return 0
"""

_RECORD_SUCCESS_SCRIPT = """
local key = KEYS[1]
redis.call('HSET', key, 'state', 'closed', 'failures', '0', 'probe_taken', '0')
return 1
"""

_RECORD_FAILURE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])

local state = redis.call('HGET', key, 'state')
if state == false then
    state = 'closed'
end

if state == 'half_open' then
    -- the probe failed -- reopen for another full recovery window
    redis.call('HSET', key, 'state', 'open', 'opened_at', now, 'failures', 0, 'probe_taken', '0')
    return 'open'
end

local failures = redis.call('HINCRBY', key, 'failures', 1)
if failures >= threshold then
    redis.call('HSET', key, 'state', 'open', 'opened_at', now, 'probe_taken', '0')
    return 'open'
end
return 'closed'
"""


class CircuitBreaker:
    """
    Redis-backed circuit breaker, one independent circuit per key (we key by
    channel name -- an email provider outage shouldn't throttle SMS sends).
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        failure_threshold: int,
        recovery_seconds: int,
        now_fn=time.time,
    ):
        self.redis = redis_client
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._now_fn = now_fn

    def _key(self, name: str) -> str:
        return f"circuit_breaker:{name}"

    def allow_request(self, name: str) -> bool:
        allowed = self.redis.eval(
            _ALLOW_REQUEST_SCRIPT, 1, self._key(name), self._now_fn(), self.recovery_seconds
        )
        return bool(allowed)

    def record_success(self, name: str) -> None:
        self.redis.eval(_RECORD_SUCCESS_SCRIPT, 1, self._key(name))

    def record_failure(self, name: str) -> None:
        self.redis.eval(
            _RECORD_FAILURE_SCRIPT, 1, self._key(name), self._now_fn(), self.failure_threshold
        )
