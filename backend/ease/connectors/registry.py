"""Connector registry - drives the router's API-vs-browser choice and the planner's capability manifest.

To add a service: write a Connector subclass and add it to CONNECTOR_CLASSES.
"""

from __future__ import annotations

from functools import lru_cache

from ease.connectors.base import Connector
from ease.connectors.jobs import GreenhouseConnector, LeverConnector
from ease.connectors.productivity import NotionConnector, SheetsConnector
from ease.connectors.research import ArxivConnector, OpenAlexConnector
from ease.connectors.search import ShoppingConnector, WebSearchConnector

CONNECTOR_CLASSES: list[type[Connector]] = [
    ArxivConnector,
    OpenAlexConnector,
    GreenhouseConnector,
    LeverConnector,
    NotionConnector,
    SheetsConnector,
    WebSearchConnector,
    ShoppingConnector,
    # TelegramConnector and SlackConnector: see GitHub issues (team tasks)
]


@lru_cache
def registry() -> dict[str, Connector]:
    return {cls.service: cls() for cls in CONNECTOR_CLASSES}


def get_connector(service: str) -> Connector | None:
    return registry().get(service)
