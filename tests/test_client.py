"""Client tests. No network: a fake opener stands in for urlopen."""
import io
import json
import urllib.error

import pytest

from analyzer.client import (
    BlockedError,
    HttpError,
    PaulsjobClient,
    TransportError,
)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, body, headers=None):
    return urllib.error.HTTPError(
        url="http://test",
        code=status,
        msg="err",
        hdrs=headers or {},
        fp=io.BytesIO(body.encode() if isinstance(body, str) else body),
    )


def make_client(responses, **kwargs):
    """`responses` is a list of either bytes (success) or exceptions to raise."""
    calls = []

    def opener(request, timeout):
        calls.append(request)
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    client = PaulsjobClient(
        "https://api.example/dev/v1",
        "secret-key",
        opener=opener,
        sleep=lambda _s: None,
        **kwargs,
    )
    return client, calls


def envelope(data):
    return json.dumps({"status": 200, "message": "success", "data": data}).encode()


class TestHeaders:
    def test_sends_api_key_and_user_agent(self):
        client, calls = make_client([envelope({})])
        client.get("/thing")
        headers = calls[0].headers
        assert headers["X-company-api-key"] == "secret-key"
        # A default urllib User-Agent is what triggered the Cloudflare block.
        assert "urllib" not in headers["User-agent"].lower()

    def test_correlation_id_is_stable_request_id_is_not(self):
        client, calls = make_client([envelope({})])
        client.get("/a")
        client.get("/b")
        assert calls[0].headers["Paul-correlation-id"] == calls[1].headers["Paul-correlation-id"]
        assert calls[0].headers["Paul-request-id"] != calls[1].headers["Paul-request-id"]


class TestEnvelope:
    def test_unwraps_data(self):
        client, _ = make_client([envelope({"Jobs": [1, 2]})])
        assert client.get("/jobs") == {"Jobs": [1, 2]}

    def test_returns_raw_when_no_data_key(self):
        client, _ = make_client([json.dumps({"Categories": []}).encode()])
        assert client.get("/x") == {"Categories": []}

    def test_empty_body_is_not_a_crash(self):
        client, _ = make_client([b""])
        assert client.get("/x") == {}


class TestErrorTaxonomy:
    def test_cloudflare_block_is_distinct_from_api_error(self):
        body = json.dumps(
            {"title": "Error 1010: Access denied", "status": 403, "error_code": 1010,
             "detail": "The site owner has blocked access based on your browser's signature."}
        )
        client, _ = make_client([http_error(403, body)])
        with pytest.raises(BlockedError) as info:
            client.get("/recruiting/jobs")
        assert "User-Agent" in str(info.value)

    def test_api_403_reports_the_apis_own_message(self):
        body = json.dumps({"status": 403, "message": "missing permission job:view"})
        client, _ = make_client([http_error(403, body)])
        with pytest.raises(HttpError) as info:
            client.get("/recruiting/jobs")
        assert "job:view" in str(info.value)
        assert not isinstance(info.value, BlockedError)

    def test_plain_text_404_does_not_crash_the_parser(self):
        client, _ = make_client([http_error(404, "404 page not found")])
        with pytest.raises(HttpError) as info:
            client.get("/recruiting/jobs")
        assert info.value.status == 404


class TestRetries:
    def test_retries_5xx_then_succeeds(self):
        responses = [http_error(503, "nope"), envelope({"ok": True})]
        seq = iter(responses)

        def opener(request, timeout):
            item = next(seq, responses[-1])
            if isinstance(item, Exception):
                raise item
            return FakeResponse(item)

        client = PaulsjobClient("https://x", "k", opener=opener, sleep=lambda _s: None)
        assert client.get("/y") == {"ok": True}
        assert client.stats.retries == 1

    def test_gives_up_after_max_retries(self):
        client, _ = make_client([http_error(500, "boom")], max_retries=2)
        with pytest.raises(HttpError):
            client.get("/y")
        assert client.stats.retries == 2

    def test_does_not_retry_a_400(self):
        client, calls = make_client([http_error(400, json.dumps({"message": "bad"}))])
        with pytest.raises(HttpError):
            client.get("/y")
        assert len(calls) == 1

    def test_honours_retry_after(self):
        slept = []
        responses = [http_error(429, "slow down", {"Retry-After": "7"}), envelope({})]
        seq = iter(responses)

        def opener(request, timeout):
            item = next(seq, responses[-1])
            if isinstance(item, Exception):
                raise item
            return FakeResponse(item)

        client = PaulsjobClient("https://x", "k", opener=opener, sleep=slept.append)
        client.get("/y")
        assert slept == [7.0]

    def test_transport_failure_becomes_readable_error(self):
        client, _ = make_client([urllib.error.URLError("timed out")], max_retries=1)
        with pytest.raises(TransportError) as info:
            client.get("/y")
        assert "no response from server" in str(info.value)


class TestPagination:
    def test_cursor_pagination_follows_key_to_the_end(self):
        pages = [
            envelope({"Items": [{"i": 1}], "LastEvaluatedKey": "k1"}),
            envelope({"Items": [{"i": 2}], "LastEvaluatedKey": ""}),
        ]
        seq = iter(pages)

        def opener(request, timeout):
            return FakeResponse(next(seq, pages[-1]))

        client = PaulsjobClient("https://x", "k", opener=opener, sleep=lambda _s: None)
        assert list(client.paginate_cursor("/things", "Items")) == [{"i": 1}, {"i": 2}]

    def test_page_pagination_stops_at_total_page(self):
        pages = [
            envelope({"Rows": [{"i": 1}], "TotalPage": 2}),
            envelope({"Rows": [{"i": 2}], "TotalPage": 2}),
            envelope({"Rows": [{"i": 3}], "TotalPage": 2}),
        ]
        seq = iter(pages)

        def opener(request, timeout):
            return FakeResponse(next(seq, pages[-1]))

        client = PaulsjobClient("https://x", "k", opener=opener, sleep=lambda _s: None)
        got = list(client.paginate_pages("/search", "Rows", per_page=1))
        assert got == [{"i": 1}, {"i": 2}]

    def test_never_reports_page_one_only(self):
        """The failure mode the brief calls out explicitly."""
        pages = [
            envelope({"Items": [{"i": n}], "LastEvaluatedKey": f"k{n}"}) for n in range(1, 4)
        ] + [envelope({"Items": [{"i": 4}], "LastEvaluatedKey": None})]
        seq = iter(pages)

        def opener(request, timeout):
            return FakeResponse(next(seq, pages[-1]))

        client = PaulsjobClient("https://x", "k", opener=opener, sleep=lambda _s: None)
        assert len(list(client.paginate_cursor("/things", "Items"))) == 4


class TestErrorMessagesAreActionable:
    """An error must always tell the operator something they can act on."""

    def test_pascal_case_message_is_found(self):
        body = json.dumps({"Status": 400, "Message": "JobPositionTitle is required"})
        client, _ = make_client([http_error(400, body)])
        with pytest.raises(HttpError) as info:
            client.post("/recruiting/jobs")
        assert "JobPositionTitle is required" in str(info.value)

    def test_validation_details_are_surfaced(self):
        body = json.dumps({
            "Status": 400,
            "Message": "Validation error",
            "Errors": ["Published is required", "Expired is required"],
        })
        client, _ = make_client([http_error(400, body)])
        with pytest.raises(HttpError) as info:
            client.post("/recruiting/jobs")
        text = str(info.value)
        assert "Validation error" in text and "Published is required" in text

    def test_unparseable_error_still_shows_the_body(self):
        """The 'HTTP 400: HTTP 400' failure mode must be impossible."""
        body = json.dumps({"unexpected": {"shape": True}})
        client, _ = make_client([http_error(400, body)])
        with pytest.raises(HttpError) as info:
            client.post("/recruiting/jobs")
        text = str(info.value)
        assert "response body" in text and "unexpected" in text

    def test_message_as_a_list_is_joined(self):
        """This API returns `message` as a list on validation failures."""
        body = json.dumps({
            "message": ["Invalid type for field JobPositionDescription. Expected type: JobPositionDescription"],
            "status": 400,
        })
        client, _ = make_client([http_error(400, body)])
        with pytest.raises(HttpError) as info:
            client.post("/recruiting/jobs")
        assert "Invalid type for field JobPositionDescription" in str(info.value)


class TestRedirects:
    """urllib refuses to follow a redirect for POST, so the client must."""

    def _client(self, responses):
        seq = iter(responses)
        seen = []

        def opener(request, timeout):
            seen.append((request.get_method(), request.full_url, request.data))
            item = next(seq, responses[-1])
            if isinstance(item, Exception):
                raise item
            return FakeResponse(item)

        return PaulsjobClient("https://x/v1", "k", opener=opener, sleep=lambda _s: None), seen

    def test_307_preserves_method_and_body(self):
        client, seen = self._client([
            http_error(307, "", {"Location": "https://x/v1/company/person/"}),
            envelope({"Slug": "abc"}),
        ])
        assert client.post("/company/person", json_body={"FirstName": "Anna"}) == {"Slug": "abc"}
        assert seen[0][0] == "POST" and seen[1][0] == "POST"
        assert seen[1][1].endswith("/company/person/")
        assert seen[1][2] == seen[0][2]  # body resent unchanged

    def test_303_downgrades_to_get(self):
        client, seen = self._client([
            http_error(303, "", {"Location": "https://x/v1/thing"}),
            envelope({"ok": True}),
        ])
        client.post("/other", json_body={"a": 1})
        assert seen[1][0] == "GET" and seen[1][2] is None

    def test_redirect_loop_is_bounded(self):
        client, _ = self._client([http_error(307, "", {"Location": "https://x/v1/loop"})])
        with pytest.raises(HttpError) as info:
            client.post("/loop", json_body={})
        assert info.value.status == 307

    def test_redirect_does_not_consume_retry_budget(self):
        client, seen = self._client([
            http_error(307, "", {"Location": "https://x/v1/a/"}),
            http_error(503, "later"),
            envelope({"ok": True}),
        ])
        assert client.post("/a", json_body={}) == {"ok": True}
        assert client.stats.retries == 1


class TestRedirectSafety:
    """The API key is a header on every request, so a redirect to another
    origin would hand the key to that origin. Refuse, do not follow."""

    def _client(self, responses):
        seq = iter(responses)
        seen = []

        def opener(request, timeout):
            seen.append((request.get_method(), request.full_url, dict(request.header_items())))
            item = next(seq, responses[-1])
            if isinstance(item, Exception):
                raise item
            return FakeResponse(item)

        return PaulsjobClient("https://x/v1", "k", opener=opener, sleep=lambda _s: None), seen

    def test_cross_host_redirect_is_refused(self):
        client, seen = self._client([
            http_error(307, "", {"Location": "https://evil.example/v1/company/person"}),
            envelope({"Slug": "abc"}),
        ])
        with pytest.raises(HttpError) as info:
            client.post("/company/person", json_body={"FirstName": "Anna"})
        assert "different origin" in str(info.value)
        assert len(seen) == 1  # nothing was sent to the other host

    def test_scheme_downgrade_is_refused(self):
        client, seen = self._client([
            http_error(301, "", {"Location": "http://x/v1/thing"}),
            envelope({"ok": True}),
        ])
        with pytest.raises(HttpError):
            client.get("/thing")
        assert len(seen) == 1

    def test_same_origin_redirect_still_followed(self):
        client, seen = self._client([
            http_error(307, "", {"Location": "/v1/company/person/"}),
            envelope({"Slug": "abc"}),
        ])
        assert client.post("/company/person", json_body={}) == {"Slug": "abc"}
        assert seen[1][1] == "https://x/v1/company/person/"
        assert seen[1][2].get("X-company-api-key") == "k"


class TestRetryAfterCap:
    def test_retry_after_is_capped(self):
        from analyzer.client import MAX_RETRY_AFTER
        slept = []
        client, _ = make_client(
            [http_error(503, "", {"Retry-After": "86400"}), envelope({"ok": True})]
        )
        client._sleep = slept.append
        assert client.get("/x") == {"ok": True}
        assert slept == [MAX_RETRY_AFTER]

    def test_small_retry_after_is_honoured(self):
        slept = []
        client, _ = make_client(
            [http_error(429, "", {"Retry-After": "2"}), envelope({"ok": True})]
        )
        client._sleep = slept.append
        client.get("/x")
        assert slept == [2.0]


class TestPathSegment:
    def test_encodes_separators_and_traversal(self):
        from analyzer.client import path_segment
        assert path_segment("a/b") == "a%2Fb"
        assert path_segment("../x") == "..%2Fx"
        assert path_segment("id?x=1") == "id%3Fx%3D1"
        assert path_segment(182760) == "182760"
        assert path_segment("plain-slug_1") == "plain-slug_1"
