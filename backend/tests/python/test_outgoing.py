from types import SimpleNamespace

import httpx
import pytest

from club_api.core.errors import ApiError
from club_api.core.outgoing import JSON, UNAVAILABLE, get_bytes, get_html

FAILURES = (httpx.ConnectError("нет связи"), httpx.ReadTimeout("нет ответа"))


@pytest.mark.parametrize("failure", FAILURES)
async def test_network_failure_is_reported_as_unavailable_source(failure):
    def fail(request):
        raise failure

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(ApiError) as error:
            await get_html(SimpleNamespace(client=client), "https://www.hse.ru/edu/dpo/", allowed_hosts={"www.hse.ru"})
    assert (error.value.status, error.value.message) == (502, UNAVAILABLE)


async def test_page_is_returned_when_source_answers():
    def answer(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>каталог</p>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        html = await get_html(
            SimpleNamespace(client=client), "https://www.hse.ru/edu/dpo/", allowed_hosts={"www.hse.ru"}
        )
    assert html == "<p>каталог</p>"


@pytest.mark.parametrize(
    "url,port,allowed",
    [
        ("https://dpo.example.test/api", 443, True),
        ("https://dpo.example.test:443/api", 443, True),
        ("https://dpo.example.test:8443/api", 443, False),
        ("https://dpo.example.test:8443/api", 8443, True),
        ("https://dpo.example.test/api", 8443, False),
    ],
)
async def test_source_port_must_match_allowed_port(url, port, allowed):
    def answer(request):
        return httpx.Response(200, headers={"content-type": JSON}, text="{}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        request = get_bytes(
            SimpleNamespace(client=client),
            url,
            allowed_hosts={"dpo.example.test"},
            max_bytes=100,
            accept=JSON,
            port=port,
        )
        if allowed:
            assert await request == b"{}"
        else:
            with pytest.raises(ApiError):
                await request
