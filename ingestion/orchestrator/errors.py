class IngestStrictError(Exception):
    """Raised when --strict is set and a create-path entity already exists in OpenGIN."""
