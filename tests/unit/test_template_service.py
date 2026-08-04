import pytest

from app.services.template_service import MissingTemplateVariableError, render_template


def test_render_template_substitutes_variables():
    result = render_template("Hello {{name}}, order {{order_id}} shipped!", {"name": "Priya", "order_id": "A1"})
    assert result == "Hello Priya, order A1 shipped!"


def test_render_template_handles_repeated_placeholder():
    result = render_template("{{x}} + {{x}} = 2x", {"x": "5"})
    assert result == "5 + 5 = 2x"


def test_render_template_no_placeholders_returns_unchanged():
    assert render_template("No variables here.", {}) == "No variables here."


def test_render_template_raises_on_missing_variable():
    with pytest.raises(MissingTemplateVariableError) as exc_info:
        render_template("Hello {{name}}", {})
    assert "name" in exc_info.value.missing_keys


def test_render_template_raises_listing_all_missing_variables():
    with pytest.raises(MissingTemplateVariableError) as exc_info:
        render_template("{{a}} and {{b}}", {"a": "1"})
    assert exc_info.value.missing_keys == ["b"]


def test_render_template_ignores_extra_unused_variables():
    # Extra variables the caller passed but the template doesn't use are fine.
    result = render_template("Hi {{name}}", {"name": "Sam", "unused": "x"})
    assert result == "Hi Sam"


def test_render_template_coerces_non_string_values():
    result = render_template("Count: {{count}}", {"count": 42})
    assert result == "Count: 42"
