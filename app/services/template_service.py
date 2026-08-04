import re

_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class MissingTemplateVariableError(Exception):
    def __init__(self, missing_keys: list[str]):
        self.missing_keys = missing_keys
        super().__init__(f"Missing template variables: {', '.join(missing_keys)}")


def render_template(text: str, variables: dict) -> str:
    """
    Replaces {{key}} placeholders in `text` with values from `variables`.
    Raises MissingTemplateVariableError if any placeholder has no matching
    variable -- silently sending "Hello {{name}}" to a user is worse than
    failing the request loudly at creation time.
    """
    found_keys = set(_PLACEHOLDER_PATTERN.findall(text))
    missing = sorted(k for k in found_keys if k not in variables)
    if missing:
        raise MissingTemplateVariableError(missing)

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        return str(variables[key])

    return _PLACEHOLDER_PATTERN.sub(_replace, text)
