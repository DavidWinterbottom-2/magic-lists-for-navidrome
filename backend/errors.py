"""Shared helpers for turning exceptions into something worth logging."""


def describe_exception(exc: Exception) -> str:
    """Render an exception for logs, falling back to its type name.

    httpx timeouts stringify to "", which produced log lines that ended at
    "Network error connecting to Navidrome:" with no reason attached — the least
    useful possible message for the failure most likely to need diagnosing.
    """
    text = str(exc).strip()
    return text if text else type(exc).__name__
