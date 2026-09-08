"""Developer/utility scripts that are also importable from the application.

A real package rather than an implicit namespace one, matching app/, pipeline/ and harness/: a
regular package in the repo root takes unambiguous precedence over anything named `scripts` that
happens to be installed in site-packages, which a namespace package does not.
"""
