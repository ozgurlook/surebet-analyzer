"""Synthetic data mode: fake sources that let the PWA run without any network access."""

from .collector import DEMO_SOURCES, DemoCollector, demo_collector_funcs
from .seed import DEMO_USERNAME, seed_demo_account

__all__ = [
    "DEMO_SOURCES",
    "DEMO_USERNAME",
    "DemoCollector",
    "demo_collector_funcs",
    "seed_demo_account",
]
