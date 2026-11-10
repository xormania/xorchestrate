"""Operator layer: projects, their machine-local instances, the companion and trust.

A project tracks its configuration in ``<repo>/.xorchestrate/``. This package
finds the project from a working directory, keeps the per-user home
(``~/.xorchestrate`` or ``$XORCHESTRATE_HOME``), the user's companion
repository (overlays and approvals), approval of the tracked configuration,
rendering to ``config.rendered.json`` and the release handoff. It hands
composition an explicit ``ProjectContext``; composition never imports it.
"""
