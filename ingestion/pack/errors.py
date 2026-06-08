class PackLoadError(Exception):
    """Raised when a ministry data pack cannot be loaded."""


class PackSchemaError(Exception):
    """Raised when the global pack schema is invalid or missing required fields."""
