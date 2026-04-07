"""Typed exceptions for the publication source layer."""


class SourceUnavailableError(Exception):
    """Raised when a source API is unreachable or returns a server error."""


class RateLimitedError(Exception):
    """Raised when a source returns 429 / rate-limit headers."""


class SchemaDriftError(Exception):
    """Raised when a source response doesn't match the expected schema."""


class NotReusableFullTextError(Exception):
    """Raised when full-text reuse is not clearly permitted for a record."""


class IdentifierMismatchError(Exception):
    """Raised when identifier lookup returns a record that doesn't match the query."""
