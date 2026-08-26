"""Brain eval harness (S13) — opt-in, OUTSIDE the quality gate.

Deselected by pytest.ini's `addopts = -m "not live and not eval"`; run
explicitly with `python -m pytest -m eval -s`. Costs real money (live
Qwen calls), so it can never turn the deterministic gate red.
"""
