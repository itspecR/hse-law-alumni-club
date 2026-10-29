from types import SimpleNamespace

import httpx
import pytest

from club_api.core.errors import ApiError
from club_api.core.outgoing import UNAVAILABLE, get_html

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
