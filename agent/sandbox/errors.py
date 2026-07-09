"""Sandbox client errors."""


class SandboxError(RuntimeError):
    """Base sandbox error."""


class SandboxDeniedError(SandboxError):
    """Raised when sandbox policy denies a task."""


class SandboxUnavailableError(SandboxError):
    """Raised when the sandbox server cannot be reached."""