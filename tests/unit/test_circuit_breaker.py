from app.services.circuit_breaker import CircuitBreaker


def _breaker(fake_redis, clock, failure_threshold=3, recovery_seconds=60):
    return CircuitBreaker(
        fake_redis,
        failure_threshold=failure_threshold,
        recovery_seconds=recovery_seconds,
        now_fn=lambda: clock["t"],
    )


def test_starts_closed_and_allows_requests(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock)
    assert breaker.allow_request("email") is True


def test_stays_closed_below_failure_threshold(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock, failure_threshold=3)
    breaker.record_failure("email")
    breaker.record_failure("email")
    assert breaker.allow_request("email") is True


def test_opens_after_reaching_failure_threshold(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock, failure_threshold=3)
    breaker.record_failure("email")
    breaker.record_failure("email")
    breaker.record_failure("email")
    assert breaker.allow_request("email") is False


def test_success_resets_failure_count(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock, failure_threshold=3)
    breaker.record_failure("email")
    breaker.record_failure("email")
    breaker.record_success("email")
    breaker.record_failure("email")
    breaker.record_failure("email")
    # Only 2 consecutive failures since the reset -- still under threshold.
    assert breaker.allow_request("email") is True


def test_stays_open_before_recovery_window_elapses(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock, failure_threshold=1, recovery_seconds=60)
    breaker.record_failure("email")
    clock["t"] = 30.0  # only halfway through the recovery window
    assert breaker.allow_request("email") is False


def test_transitions_to_half_open_after_recovery_window(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock, failure_threshold=1, recovery_seconds=60)
    breaker.record_failure("email")
    clock["t"] = 60.0
    assert breaker.allow_request("email") is True  # the one probe is let through


def test_half_open_only_allows_a_single_probe(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock, failure_threshold=1, recovery_seconds=60)
    breaker.record_failure("email")
    clock["t"] = 60.0
    assert breaker.allow_request("email") is True  # the probe
    assert breaker.allow_request("email") is False  # a second concurrent caller is rejected


def test_successful_probe_closes_the_circuit(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock, failure_threshold=1, recovery_seconds=60)
    breaker.record_failure("email")
    clock["t"] = 60.0
    breaker.allow_request("email")  # consumes the probe slot
    breaker.record_success("email")
    assert breaker.allow_request("email") is True


def test_failed_probe_reopens_the_circuit(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock, failure_threshold=1, recovery_seconds=60)
    breaker.record_failure("email")
    clock["t"] = 60.0
    breaker.allow_request("email")  # consumes the probe slot
    breaker.record_failure("email")  # probe failed
    assert breaker.allow_request("email") is False  # back to open, fresh recovery window


def test_channels_have_independent_circuits(fake_redis):
    clock = {"t": 0.0}
    breaker = _breaker(fake_redis, clock, failure_threshold=1)
    breaker.record_failure("email")
    assert breaker.allow_request("email") is False
    assert breaker.allow_request("sms") is True
