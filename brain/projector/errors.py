class ProjectorError(RuntimeError):
    """Projection failed without changing canonical journal."""


class RebuildRequired(ProjectorError):
    """Derived store is incompatible or inconsistent; operator must rebuild."""
