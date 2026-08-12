"""Test doubles for the services the app talks to.

Shared by both suites: the integration tests in tests/ drive the API against
them in-process, and the browser tests in e2e/ drive the whole app against the
same fixed library. One definition, so a behaviour change lands in both places
at once.
"""
