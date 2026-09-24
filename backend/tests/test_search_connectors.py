import json

import httpx
import pytest

from ease.connectors.base import ConnectorContext, ConnectorError
from ease.connectors.search import ShoppingConnector, WebSearchConnector, price_value
from ease.security import netguard


@pytest.fixture(autouse=True)
def offline_dns(monkeypatch):
    monkeypatch.setattr(netguard, "_resolve", lambda host: ("93.184.215.14",))


def ctx(secrets):
    return ConnectorContext(user_id="u", task_id="t", step_key="s", secret=secrets.get)


@pytest.mark.parametrize("text,value", [
    ("₹4,295.00", 4295.0), ("$12.99", 12.99), ("Rs. 3,999", 3999.0), ("€1.234,56", 1234.56),
    ("free", None), (None, None),
])
def test_price_value(text, value):
    assert price_value(text) == value


def test_shopping_sorts_cheapest_first_and_sends_key_in_header():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"shopping": [
            {"title": "Casio MTP-E740", "source": "Store A", "price": "₹5,495.00", "link": "https://a.example/1",
             "rating": 4.5, "ratingCount": 120},
            {"title": "Casio MTP-E740", "source": "Store B", "price": "₹4,295.00", "link": "https://b.example/2"},
            {"title": "Casio MTP-E740", "source": "Store C", "price": "see site", "link": "https://c.example/3"},
        ]})

    conn = ShoppingConnector(transport=httpx.MockTransport(handler))
    out = conn.call("search", {"query": "Casio MTP-E740"}, ctx({"serper:default": "serper-test-key"}))
    assert [i["store"] for i in out["items"]] == ["Store B", "Store A", "Store C"]
    assert seen[0].headers["x-api-key"] == "serper-test-key"
    assert json.loads(seen[0].content) == {"q": "Casio MTP-E740", "gl": "in", "num": 10}


def test_web_search_maps_results_and_uses_bearer():
    def handler(req):
        assert req.headers["authorization"] == "Bearer tvly-test"
        return httpx.Response(200, json={"answer": "yes", "results": [
            {"title": "Casio official", "url": "https://www.casio.com/", "content": "Watches", "score": 0.9}]})

    out = WebSearchConnector(transport=httpx.MockTransport(handler)).call(
        "search", {"query": "casio official store"}, ctx({"tavily:default": "tvly-test"}))
    assert out["items"][0]["url"] == "https://www.casio.com/" and out["answer"] == "yes"


def test_not_configured_is_hidden_and_raises():
    from ease.graph.manifest import available_tools

    tools = available_tools(lambda ref: None)
    assert "api.shopping.search" not in tools and "api.websearch.search" not in tools
    with pytest.raises(ConnectorError):
        ShoppingConnector().call("search", {"query": "x y"}, ctx({}))
