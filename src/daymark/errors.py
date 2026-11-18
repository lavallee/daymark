"""Stable exceptions shared by the CLI, HTTP client and task engine."""


class DomainError(ValueError):
    """A domain failure with a stable code.

    ``details`` names offending fields and rules only; it never echoes input values.
    """

    def __init__(self, code: str, message: str, details: list[str] | None = None):
        self.code = code
        self.details = details or []
        super().__init__(message)


class ClientError(Exception):
    """Safe failure containing a status code, never server bodies or credentials."""

    def __init__(self, status_code: int | None = None, *, recovery: str | None = None) -> None:
        self.status_code = status_code
        self.recovery = recovery  # Adapter-authored guidance only. Never copy a remote body here.
        self.code = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid"}.get(
            status_code, "remote"
        )
        super().__init__(f"Daymark request failed ({status_code or 'transport'})")


    @property
    def uncertain(self) -> bool:
        """A transport or service failure can hide a committed operation."""
        return self.status_code is None or self.status_code >= 500
