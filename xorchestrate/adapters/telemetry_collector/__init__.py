"""Local observation collector with replaceable corpus and artifact storage.

``domain`` holds immutable observation values, ``application`` the capture
orchestration, ``ports`` the storage contracts, and ``sqlite``/``artifacts`` the
default ``sqlite`` corpus and ``filesystem`` artifact adapters. The corpus format
remains ``agent-telemetry-v1`` so existing corpora and bundles stay readable.
"""
