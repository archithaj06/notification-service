from app.services.channels.base import CircuitBreakerSender, SendResult
from app.services.circuit_breaker import CircuitBreaker


class _StubSender:
    def __init__(self, result: SendResult):
        self.result = result
        self.calls = 0

    def send(self, *, recipient, subject, body):
        self.calls += 1
        return self.result


def _breaker(fake_redis, failure_threshold=3, recovery_seconds=60):
    return CircuitBreaker(fake_redis, failure_threshold=failure_threshold, recovery_seconds=recovery_seconds)


def test_delegates_to_inner_sender_when_circuit_closed(fake_redis):
    inner = _StubSender(SendResult(success=True, provider_response={}))
    sender = CircuitBreakerSender(inner, _breaker(fake_redis), "email")

    result = sender.send(recipient="user@example.com", subject=None, body="hi")

    assert result.success is True
    assert inner.calls == 1


def test_records_failure_on_inner_send_failure(fake_redis):
    inner = _StubSender(SendResult(success=False, provider_response={}, error_message="boom"))
    breaker = _breaker(fake_redis, failure_threshold=1)
    sender = CircuitBreakerSender(inner, breaker, "email")

    sender.send(recipient="user@example.com", subject=None, body="hi")

    # One failure already hit the threshold=1 -- circuit should now be open.
    assert breaker.allow_request("email") is False


def test_does_not_call_inner_sender_when_circuit_open(fake_redis):
    inner = _StubSender(SendResult(success=True, provider_response={}))
    breaker = _breaker(fake_redis, failure_threshold=1)
    breaker.record_failure("email")  # opens the circuit directly
    sender = CircuitBreakerSender(inner, breaker, "email")

    result = sender.send(recipient="user@example.com", subject=None, body="hi")

    assert result.success is False
    assert "circuit breaker" in result.error_message.lower()
    assert inner.calls == 0


def test_success_after_recovery_closes_circuit_for_next_call(fake_redis):
    inner = _StubSender(SendResult(success=True, provider_response={}))
    clock = {"t": 0.0}
    breaker = CircuitBreaker(
        fake_redis, failure_threshold=1, recovery_seconds=60, now_fn=lambda: clock["t"]
    )
    breaker.record_failure("email")
    clock["t"] = 60.0
    sender = CircuitBreakerSender(inner, breaker, "email")

    result = sender.send(recipient="user@example.com", subject=None, body="hi")

    assert result.success is True
    assert inner.calls == 1
    assert breaker.allow_request("email") is True
