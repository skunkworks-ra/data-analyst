"""
analyst_driver — the external loop.

Runs a CASA reduction as a sequence of long jobs and calls a model only at the
decision points between them. The model never waits on a job: it reads a
brief, writes one JSON decision, and exits.

Entry point: ``analyst-driver`` (see analyst_driver.driver:main).
"""

__all__ = ["driver"]
