from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.url_reader import UrlReadError, UrlReader


PUBLIC_IP = "93.184.216.34"


def resolver(hostname: str, port: int) -> tuple[str, ...]:
    return (PUBLIC_IP,)


def private_resolver(hostname: str, port: int) -> tuple[str, ...]:
    return ("127.0.0.1",)


def event(
    text: str,
    *,
    scope_type: ScopeType = ScopeType.PERSONAL,
    event_type: EventType | None = None,
    metadata: dict | None = None,
) -> EventEnvelope:
    if event_type is None:
        event_type = (
            EventType.PRIVATE_MESSAGE
            if scope_type is ScopeType.PERSONAL
            else EventType.GROUP_MESSAGE
        )
    return EventEnvelope(
        event_id="evt:url",
        event_type=event_type,
        occurred_at=datetime.now(timezone.utc),
        scope_type=scope_type,
        scope_id="u1" if scope_type is ScopeType.PERSONAL else "-1001",
        actor_user_id="u1",
        message_id="100",
        text=text,
        metadata=dict(metadata or {}),
    )


def make_reader(handler, **kwargs) -> UrlReader:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    return UrlReader(
        client=client,
        resolver=resolver,
        **kwargs,
    )


def test_direct_html_fetch_is_pinned_and_extracts_readable_article() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == PUBLIC_IP
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="""
                <html>
                  <head><title>Test story</title><script>ignore()</script></head>
                  <body>
                    <nav>navigation noise</nav>
                    <article>
                      <h1>Главный заголовок</h1>
                      <p>Это основной текст статьи, который НеНой должен увидеть.</p>
                      <p>Вторая содержательная строка делает материал достаточно длинным для чтения.</p>
                      <p>Третья строка подтверждает, что article выбран вместо навигации и футера.</p>
                    </article>
                    <footer>footer noise</footer>
                  </body>
                </html>
            """,
        )

    reader = make_reader(handler)
    result = reader.read("https://example.com/story")

    assert result.source == "direct"
    assert result.title == "Test story"
    assert "Главный заголовок" in result.content
    assert "основной текст статьи" in result.content
    assert "navigation noise" not in result.content
    assert "footer noise" not in result.content
    assert "ignore()" not in result.content
    assert len(calls) == 1


def test_personal_current_url_is_explicit_handoff() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="A" * 200,
        )

    reader = make_reader(handler)
    bundle = reader.read_for_event(event("https://example.com/a"))

    assert bundle.status == "succeeded"
    assert bundle.result is not None


def test_personal_reply_url_requires_read_intent() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="B" * 200,
        )

    reader = make_reader(handler)
    no_intent = reader.read_for_event(
        event(
            "ага",
            metadata={"reply_to_text": "https://example.com/a"},
        )
    )
    with_intent = reader.read_for_event(
        event(
            "НеНой, глянь, что там",
            metadata={"reply_to_text": "https://example.com/a"},
        )
    )

    assert no_intent.status == "not_requested"
    assert with_intent.status == "succeeded"
    assert len(calls) == 1


def test_group_ambient_link_never_fetches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("ambient group URL must not be fetched")

    reader = make_reader(handler)
    bundle = reader.read_for_event(
        event(
            "https://example.com/a",
            scope_type=ScopeType.GROUP,
        )
    )

    assert bundle.status == "not_requested"


def test_group_direct_address_can_read_current_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="C" * 200,
        )

    reader = make_reader(handler)
    bundle = reader.read_for_event(
        event(
            "НеНой, глянь https://example.com/a",
            scope_type=ScopeType.GROUP,
            event_type=EventType.DIRECT_MENTION,
            metadata={"direct_mention": True},
        )
    )

    assert bundle.status == "succeeded"


def test_group_direct_address_can_read_replied_to_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="D" * 200,
        )

    reader = make_reader(handler)
    bundle = reader.read_for_event(
        event(
            "НеНой, что думаешь?",
            scope_type=ScopeType.GROUP,
            event_type=EventType.DIRECT_MENTION,
            metadata={
                "direct_mention": True,
                "reply_to_text": "Вот статья https://example.com/a",
            },
        )
    )

    assert bundle.status == "succeeded"


def test_negative_intent_never_fetches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("negative intent must not fetch")

    reader = make_reader(handler)
    bundle = reader.read_for_event(
        event("НеНой, не открывай https://example.com/a")
    )

    assert bundle.status == "not_requested"


def test_more_than_one_url_fails_closed_without_fetch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("multiple URLs must not fetch")

    reader = make_reader(handler)
    bundle = reader.read_for_event(
        event("Сравни https://example.com/a и https://example.org/b")
    )

    assert bundle.status == "failed"
    assert bundle.reason == "too_many_urls"
    assert bundle.url_count == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "file:///etc/passwd",
        "http://example.com:8080/",
    ],
)
def test_private_hosts_schemes_and_ports_are_blocked(url: str) -> None:
    reader = make_reader(
        lambda request: (_ for _ in ()).throw(
            AssertionError("blocked URL must not make a request")
        )
    )

    with pytest.raises(UrlReadError) as exc:
        reader.read(url)

    assert exc.value.reason in {"blocked_host", "blocked_scheme", "blocked_port"}


def test_dns_result_with_any_private_address_is_blocked() -> None:
    reader = UrlReader(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    AssertionError("blocked DNS target must not make a request")
                )
            )
        ),
        resolver=private_resolver,
    )

    with pytest.raises(UrlReadError, match="blocked_host"):
        reader.read("https://example.com/a")


def test_redirect_to_private_target_is_blocked_before_second_request() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/admin"},
        )

    reader = make_reader(handler)

    with pytest.raises(UrlReadError) as exc:
        reader.read("https://example.com/a")

    assert exc.value.reason == "blocked_host"
    assert len(calls) == 1


def test_streaming_limit_blocks_oversized_download() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"Z" * 5000,
        )

    reader = make_reader(handler, max_download_bytes=2048)

    with pytest.raises(UrlReadError) as exc:
        reader.read("https://example.com/a")

    assert exc.value.reason == "download_too_large"


def test_cache_avoids_second_fetch_and_marks_cache_hit() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="Cache body " * 30,
        )

    reader = make_reader(handler, cache_ttl_seconds=3600)
    first = reader.read("https://example.com/a")
    second = reader.read("https://example.com/a")

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert len(calls) == 1


def test_firecrawl_is_optional_fallback_for_direct_fetch_failure() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.host, str(request.url)))
        if request.method == "GET":
            assert request.url.host == PUBLIC_IP
            return httpx.Response(403, text="blocked")
        assert request.method == "POST"
        assert request.url.host == "api.firecrawl.dev"
        assert request.url.path == "/v2/scrape"
        assert request.headers["authorization"] == "Bearer fc-test"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "Firecrawl content " * 20,
                    "metadata": {
                        "title": "Rendered page",
                        "sourceURL": "https://example.com/a",
                    },
                },
            },
        )

    reader = make_reader(handler, firecrawl_api_key="fc-test")
    result = reader.read("https://example.com/a")

    assert result.source == "firecrawl"
    assert result.title == "Rendered page"
    assert "Firecrawl content" in result.content
    assert [item[0] for item in calls] == ["GET", "POST"]


def test_content_is_truncated_to_configured_character_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text=("line of useful article text " * 500),
        )

    reader = make_reader(handler, max_content_chars=1200)
    result = reader.read("https://example.com/a")

    assert result.truncated is True
    assert len(result.content) < 1300
    assert "страница обрезана" in result.content



def test_persistable_telemetry_omits_url_path_query_and_page_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="Safe article body " * 20,
        )

    reader = make_reader(handler)
    bundle = reader.read_for_event(
        event("https://example.com/private/path?token=super-secret")
    )

    assert bundle.status == "succeeded"
    telemetry = bundle.as_telemetry()
    assert telemetry["host"] == "example.com"
    serialized = repr(telemetry)
    assert "private/path" not in serialized
    assert "super-secret" not in serialized
    assert "Safe article body" not in serialized
    assert "requested_url" not in telemetry
    assert "final_url" not in telemetry
