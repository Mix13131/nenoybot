from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope


_URL_RE = re.compile(r"""https?://[^\s<>"']+""", flags=re.IGNORECASE)
_NEGATIVE_INTENT_RE = re.compile(
    r"(?i)\b(?:не\s+(?:открывай|читай|переходи|смотри|проверяй|разбирай)|"
    r"не\s+надо\s+(?:открывать|читать|переходить|смотреть|проверять|разбирать))\b"
)
_READ_INTENT_RE = re.compile(
    r"(?i)\b(?:"
    r"глянь|посмотри|прочитай|открой|проверь|разбери|проанализируй|"
    r"что\s+(?:там|думаешь|скажешь)|о\s+ч[её]м\s+(?:там|это)|"
    r"это\s+(?:правда|что)|правда\s+ли|верить|достоверн\\w*|"
    r"summar(?:y|ize)|read|check|look\s+at|what\s+do\s+you\s+think"
    r")\b"
)
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_ALLOWED_PORTS = {80, 443}
_ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/json",
}
_FALLBACK_REASONS = {
    "network_error",
    "redirect_limit",
    "http_status",
    "unsupported_content_type",
    "download_too_large",
    "insufficient_content",
    "decode_error",
}


class UrlReadError(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class UrlReadResult:
    url: str
    final_url: str
    title: str | None
    content: str
    source: str
    content_type: str
    fetched_at: str
    truncated: bool
    download_bytes: int
    cache_hit: bool = False

    def as_external_context(self) -> dict[str, Any]:
        return {
            "kind": "web_page",
            "url": self.url,
            "final_url": self.final_url,
            "title": self.title,
            "source": self.source,
            "content_type": self.content_type,
            "fetched_at": self.fetched_at,
            "truncated": self.truncated,
            "content": self.content,
        }

    def as_metadata(self) -> dict[str, Any]:
        return {
            "status": "succeeded",
            "requested_url": self.url,
            "final_url": self.final_url,
            "source": self.source,
            "content_type": self.content_type,
            "cache_hit": self.cache_hit,
            "truncated": self.truncated,
            "content_chars": len(self.content),
            "download_bytes": self.download_bytes,
        }

    def as_telemetry(self) -> dict[str, Any]:
        final_host = (urlsplit(self.final_url).hostname or "").lower() or None
        return {
            "status": "succeeded",
            "host": final_host,
            "source": self.source,
            "content_type": self.content_type,
            "cache_hit": self.cache_hit,
            "truncated": self.truncated,
            "content_chars": len(self.content),
            "download_bytes": self.download_bytes,
        }


@dataclass(frozen=True)
class UrlReadBundle:
    status: str
    requested_url: str | None = None
    result: UrlReadResult | None = None
    reason: str | None = None
    detail: str | None = None
    url_count: int = 0

    @classmethod
    def not_requested(cls) -> "UrlReadBundle":
        return cls(status="not_requested")

    def external_context(self) -> tuple[dict[str, Any], ...]:
        if self.status != "succeeded" or self.result is None:
            return ()
        return (self.result.as_external_context(),)

    def as_action_state(self) -> dict[str, Any]:
        if self.status == "succeeded" and self.result is not None:
            return self.result.as_metadata()
        value: dict[str, Any] = {
            "status": self.status,
            "requested_url": self.requested_url,
        }
        if self.reason:
            value["reason"] = self.reason
        if self.detail:
            value["detail"] = self.detail
        if self.url_count:
            value["url_count"] = self.url_count
        return value

    def as_telemetry(self) -> dict[str, Any]:
        if self.status == "succeeded" and self.result is not None:
            return self.result.as_telemetry()
        host = None
        if self.requested_url:
            try:
                host = (urlsplit(self.requested_url).hostname or "").lower() or None
            except ValueError:
                host = None
        value: dict[str, Any] = {
            "status": self.status,
            "host": host,
        }
        if self.reason:
            value["reason"] = self.reason
        if self.detail:
            value["detail"] = self.detail
        if self.url_count:
            value["url_count"] = self.url_count
        return value


class _ReadableHTMLParser(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form"}
    _BLOCK = {
        "article", "main", "section", "div", "p", "br", "li", "ul", "ol",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table",
        "tr", "td", "th",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._main_depth = 0
        self._title_depth = 0
        self._all: list[str] = []
        self._main: list[str] = []
        self._title: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.lower()
        if name in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if name == "title":
            self._title_depth += 1
            return
        if name in {"article", "main"}:
            self._main_depth += 1
        if name in self._BLOCK:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in self._SKIP:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if name == "title":
            if self._title_depth:
                self._title_depth -= 1
            return
        if name in self._BLOCK:
            self._append("\n")
        if name in {"article", "main"} and self._main_depth:
            self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._title_depth:
            self._title.append(data)
            return
        self._append(data)

    def _append(self, value: str) -> None:
        self._all.append(value)
        if self._main_depth:
            self._main.append(value)

    def readable(self) -> tuple[str | None, str]:
        title = _normalize_text(" ".join(self._title)) or None
        main = _normalize_text("".join(self._main))
        all_text = _normalize_text("".join(self._all))
        content = main if len(main) >= 200 else all_text
        return title, content


def _normalize_text(value: str) -> str:
    lines: list[str] = []
    previous = None
    for raw_line in value.replace("\r", "\n").split("\n"):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if line == previous:
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines).strip()


def _extract_urls(value: str | None) -> list[str]:
    if not value:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(value):
        candidate = match.group(0).rstrip(".,!?;:)]}»”'\"")
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def _default_resolver(hostname: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise UrlReadError("dns_error", type(exc).__name__) from exc
    addresses: list[str] = []
    for item in infos:
        raw = str(item[4][0]).split("%", 1)[0]
        if raw not in addresses:
            addresses.append(raw)
    if not addresses:
        raise UrlReadError("dns_error", "no_addresses")
    return tuple(addresses)


def _public_ip(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return False
    return parsed.is_global


class UrlReader:
    """Bounded, explicit URL reader for one user-requested public page.

    The reader is intentionally not a browsing agent. It fetches at most one
    explicit URL, validates every redirect against SSRF targets, extracts text,
    and optionally falls back to Firecrawl for pages a plain HTTP fetch cannot
    read.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout_seconds: float = 8.0,
        max_download_bytes: int = 5_000_000,
        max_content_chars: int = 30_000,
        cache_ttl_seconds: int = 86_400,
        max_redirects: int = 5,
        firecrawl_api_key: str | None = None,
        firecrawl_timeout_seconds: float = 20.0,
        client: httpx.Client | None = None,
        resolver: Callable[[str, int], tuple[str, ...]] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = bool(enabled)
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self.max_download_bytes = max(1_024, int(max_download_bytes))
        self.max_content_chars = max(1_000, int(max_content_chars))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.max_redirects = max(0, min(int(max_redirects), 10))
        self.firecrawl_api_key = (firecrawl_api_key or "").strip() or None
        self.firecrawl_timeout_seconds = max(1.0, float(firecrawl_timeout_seconds))
        self._resolver = resolver or _default_resolver
        self._monotonic = monotonic
        self._cache: dict[str, tuple[float, UrlReadResult]] = {}
        self._client = client or httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "NenoyBot/2.0 URLReader",
                "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.8,*/*;q=0.2",
            },
        )

    def read_for_event(self, event: EventEnvelope) -> UrlReadBundle:
        if not self.enabled:
            return UrlReadBundle.not_requested()

        text = event.text or ""
        reply_text = (
            event.metadata.get("reply_to_text")
            if isinstance(event.metadata.get("reply_to_text"), str)
            else ""
        )
        current_urls = _extract_urls(text)
        reply_urls = _extract_urls(reply_text)

        if not self._explicit_request(event, current_urls=current_urls, reply_urls=reply_urls):
            return UrlReadBundle.not_requested()

        urls: list[str] = []
        for item in (*current_urls, *reply_urls):
            if item not in urls:
                urls.append(item)

        if len(urls) != 1:
            return UrlReadBundle(
                status="failed",
                requested_url=urls[0] if urls else None,
                reason="too_many_urls" if len(urls) > 1 else "url_missing",
                url_count=len(urls),
            )

        requested = urls[0]
        try:
            result = self.read(requested)
        except UrlReadError as exc:
            return UrlReadBundle(
                status="failed",
                requested_url=requested,
                reason=exc.reason,
                detail=exc.detail,
                url_count=1,
            )
        return UrlReadBundle(
            status="succeeded",
            requested_url=requested,
            result=result,
            url_count=1,
        )

    @staticmethod
    def _explicit_request(
        event: EventEnvelope,
        *,
        current_urls: list[str],
        reply_urls: list[str],
    ) -> bool:
        if not current_urls and not reply_urls:
            return False
        text = event.text or ""
        if _NEGATIVE_INTENT_RE.search(text):
            return False

        if event.scope_type is ScopeType.PERSONAL:
            # A URL sent directly to НеНой is itself an explicit handoff.
            if current_urls:
                return True
            return bool(reply_urls and _READ_INTENT_RE.search(text))

        explicitly_addressed = bool(
            event.metadata.get("direct_mention")
            or event.metadata.get("reply_to_bot")
            or event.event_type in {EventType.DIRECT_MENTION, EventType.REPLY_TO_BOT}
        )
        if not explicitly_addressed:
            return False

        # In Group, НеНой never follows ordinary ambient links. A direct
        # address/reply plus a current or replied-to URL is required.
        return bool(current_urls or reply_urls)

    def read(self, raw_url: str) -> UrlReadResult:
        url = self._validate_public_url(raw_url)
        cache_key = self._cache_key(url)
        cached = self._cached(cache_key)
        if cached is not None:
            return replace(cached, cache_hit=True)

        try:
            result = self._fetch_direct(url)
        except UrlReadError as exc:
            if self.firecrawl_api_key and exc.reason in _FALLBACK_REASONS:
                result = self._fetch_firecrawl(url)
            else:
                raise

        if self.cache_ttl_seconds:
            self._cache[cache_key] = (self._monotonic(), result)
        return result

    def _cached(self, key: str) -> UrlReadResult | None:
        item = self._cache.get(key)
        if item is None:
            return None
        created, result = item
        if self._monotonic() - created > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return result

    @staticmethod
    def _cache_key(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path or "/",
                parts.query,
                "",
            )
        )

    def _validate_public_url(self, raw_url: str) -> str:
        value = str(raw_url or "").strip()
        try:
            parts = urlsplit(value)
        except ValueError as exc:
            raise UrlReadError("invalid_url", type(exc).__name__) from exc

        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            raise UrlReadError("blocked_scheme", scheme or "missing")
        if parts.username is not None or parts.password is not None:
            raise UrlReadError("blocked_credentials")

        hostname = (parts.hostname or "").rstrip(".").lower()
        if not hostname:
            raise UrlReadError("invalid_url", "hostname_missing")
        if "%" in hostname:
            raise UrlReadError("blocked_host", "zone_identifier")
        if (
            hostname == "localhost"
            or hostname.endswith(".localhost")
            or hostname.endswith(".local")
            or hostname.endswith(".internal")
            or hostname.endswith(".home.arpa")
        ):
            raise UrlReadError("blocked_host", hostname)

        try:
            port = parts.port
        except ValueError as exc:
            raise UrlReadError("blocked_port", "invalid") from exc
        effective_port = port or (443 if scheme == "https" else 80)
        if effective_port not in _ALLOWED_PORTS:
            raise UrlReadError("blocked_port", str(effective_port))

        literal_ip = None
        try:
            literal_ip = ipaddress.ip_address(hostname)
        except ValueError:
            pass

        if literal_ip is not None:
            if not literal_ip.is_global:
                raise UrlReadError("blocked_host", hostname)
            normalized_host = hostname
        else:
            try:
                ascii_host = hostname.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise UrlReadError("invalid_url", "invalid_hostname") from exc
            addresses = self._resolver(ascii_host, effective_port)
            if not addresses or any(not _public_ip(item) for item in addresses):
                raise UrlReadError("blocked_host", hostname)
            normalized_host = ascii_host

        if ":" in normalized_host and not normalized_host.startswith("["):
            normalized_host = f"[{normalized_host}]"
        netloc = normalized_host
        if port is not None:
            netloc = f"{normalized_host}:{port}"

        return urlunsplit(
            (
                scheme,
                netloc,
                parts.path or "/",
                parts.query,
                "",
            )
        )

    def _pinned_request_target(
        self,
        url: str,
    ) -> tuple[httpx.URL, dict[str, str], dict[str, Any]]:
        parts = urlsplit(url)
        hostname = (parts.hostname or "").rstrip(".").lower()
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)

        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None

        if literal is not None:
            addresses = (str(literal),)
        else:
            ascii_host = hostname.encode("idna").decode("ascii")
            addresses = self._resolver(ascii_host, port)

        if not addresses or any(not _public_ip(item) for item in addresses):
            raise UrlReadError("blocked_host", hostname)

        # Connect to a validated IP instead of resolving the hostname again
        # inside httpx. This closes the DNS-rebinding TOCTOU gap while Host/SNI
        # preserve normal virtual-host and TLS certificate behavior.
        target = httpx.URL(url).copy_with(host=addresses[0])
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        if parts.port is not None:
            host_header = f"{host_header}:{parts.port}"
        return (
            target,
            {"Host": host_header},
            {"sni_hostname": hostname},
        )

    def _fetch_direct(self, url: str) -> UrlReadResult:
        current = url
        for hop in range(self.max_redirects + 1):
            current = self._validate_public_url(current)
            target_url, pinned_headers, extensions = self._pinned_request_target(current)
            try:
                with self._client.stream(
                    "GET",
                    target_url,
                    headers=pinned_headers,
                    extensions=extensions,
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in _REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location:
                            raise UrlReadError("http_status", str(response.status_code))
                        if hop >= self.max_redirects:
                            raise UrlReadError("redirect_limit")
                        current = urljoin(current, location)
                        continue

                    if response.status_code >= 400:
                        raise UrlReadError("http_status", str(response.status_code))

                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
                        raise UrlReadError("unsupported_content_type", content_type)

                    declared = response.headers.get("content-length")
                    if declared:
                        try:
                            if int(declared) > self.max_download_bytes:
                                raise UrlReadError("download_too_large", declared)
                        except ValueError:
                            pass

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_download_bytes:
                            raise UrlReadError("download_too_large", str(total))
                        chunks.append(chunk)

                    body = b"".join(chunks)
                    encoding = response.encoding or "utf-8"
            except UrlReadError:
                raise
            except (httpx.HTTPError, OSError) as exc:
                raise UrlReadError("network_error", type(exc).__name__) from exc

            try:
                decoded = body.decode(encoding, errors="replace")
            except (LookupError, UnicodeError) as exc:
                raise UrlReadError("decode_error", type(exc).__name__) from exc

            title: str | None = None
            if content_type in {"text/html", "application/xhtml+xml"} or (
                not content_type and "<html" in decoded[:1000].lower()
            ):
                parser = _ReadableHTMLParser()
                try:
                    parser.feed(decoded)
                    parser.close()
                except Exception as exc:
                    raise UrlReadError("decode_error", type(exc).__name__) from exc
                title, content = parser.readable()
                effective_type = content_type or "text/html"
            else:
                content = _normalize_text(decoded)
                effective_type = content_type or "text/plain"

            if len(content) < 80:
                raise UrlReadError("insufficient_content", str(len(content)))

            content, truncated = self._truncate(content)
            return UrlReadResult(
                url=url,
                final_url=current,
                title=title,
                content=content,
                source="direct",
                content_type=effective_type,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                truncated=truncated,
                download_bytes=len(body),
            )

        raise UrlReadError("redirect_limit")

    def _fetch_firecrawl(self, url: str) -> UrlReadResult:
        # Revalidate the user-controlled URL immediately before handing it to a
        # third-party renderer. Firecrawl itself is a fixed trusted endpoint.
        safe_url = self._validate_public_url(url)
        try:
            response = self._client.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers={
                    "Authorization": f"Bearer {self.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": safe_url,
                    "formats": ["markdown"],
                },
                timeout=self.firecrawl_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise UrlReadError("firecrawl_error", type(exc).__name__) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise UrlReadError("firecrawl_error", "missing_data")
        content = _normalize_text(str(data.get("markdown") or ""))
        if len(content) < 80:
            raise UrlReadError("insufficient_content", str(len(content)))

        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        title = _normalize_text(str(metadata.get("title") or "")) or None
        final_url = str(
            metadata.get("sourceURL")
            or metadata.get("url")
            or safe_url
        )
        content, truncated = self._truncate(content)
        return UrlReadResult(
            url=safe_url,
            final_url=final_url,
            title=title,
            content=content,
            source="firecrawl",
            content_type="text/markdown",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            truncated=truncated,
            download_bytes=len(content.encode("utf-8")),
        )

    def _truncate(self, content: str) -> tuple[str, bool]:
        if len(content) <= self.max_content_chars:
            return content, False
        clipped = content[: self.max_content_chars]
        boundary = clipped.rfind("\n")
        if boundary < self.max_content_chars // 2:
            boundary = clipped.rfind(" ")
        if boundary > 0:
            clipped = clipped[:boundary]
        return clipped.rstrip() + "\n[…страница обрезана по лимиту контекста…]", True
