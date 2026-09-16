import json

import httpx
import pytest

from scripts.verify_model_api import check_response


@pytest.mark.parametrize("status,category", [(401, "authentication"), (403, "access_denied"), (500, "provider_error")])
def test_provider_diagnostics_do_not_echo_credentials(status, category):
    response = httpx.Response(status, json={"error": {
        "message": "Invalid token (request id: request_123) sk-private-secret"}})
    with pytest.raises(RuntimeError) as caught:
        check_response(response)
    message = str(caught.value)
    assert "sk-private-secret" not in message
    assert json.loads(message) == {"http_status": status, "category": category,
                                   "provider_error": "Invalid token", "request_id": "request_123"}


def test_success_is_not_reported_as_provider_failure():
    check_response(httpx.Response(200, json={"choices": []}))
