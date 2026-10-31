"""Rate-limit bucketing: per identity, not per connection.

The regression this file locks down was found by the 50-user load test: every virtual user in the
run shares one client IP, and an IP-only bucket answered 398 requests with 429 and refused 39 of the
50 sign-ins. A whole office behind one NAT (or every request arriving through a load balancer) would
have been throttled the same way.

The rules being tested:

* an authenticated request counts against the user, so colleagues behind one IP are independent;
* anonymous traffic counts against the IP, with a limit that fits a real office;
* sign-in counts against the account *and* the IP, so credential stuffing on one user is still
  stopped while a full office can sign in at 08:00.
"""
from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core import rate_limit
from app.core.config import settings
from app.core.errors import RateLimited
from app.core.security import create_access_token


def _request(token: str = "", host: str = "10.20.30.40") -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    return Request({"type": "http", "method": "GET", "path": "/api/v1/rfqs",
                    "headers": headers, "client": (host, 51234), "scheme": "http",
                    "server": ("testserver", 80), "query_string": b""})


@pytest.fixture(autouse=True)
def _limiter_on():
    previous = settings.rate_limit_enabled
    settings.rate_limit_enabled = True
    rate_limit.reset()
    try:
        yield
    finally:
        settings.rate_limit_enabled = previous
        rate_limit.reset()


def test_identity_uses_the_signed_in_user_and_falls_back_to_the_ip():
    token = create_access_token(user_id="user-123", tenant_id="tenant-1", roles=["buyer"])
    assert rate_limit.identity(_request(token)) == "user:user-123"
    assert rate_limit.identity(_request("not-a-jwt")) == "ip:10.20.30.40"
    assert rate_limit.identity(_request("")) == "ip:10.20.30.40"


def test_fifty_colleagues_behind_one_ip_have_independent_budgets():
    """15,000 authenticated requests from a single IP: impossible under an IP-only bucket."""
    tokens = [create_access_token(user_id=f"user-{index}", tenant_id="tenant-1", roles=["buyer"])
              for index in range(50)]
    for token in tokens:
        for _ in range(200):  # under the 240/min per-user limit
            rate_limit.enforce(_request(token))
    # ... and one of them going over their own budget is the only thing that trips it.
    with pytest.raises(RateLimited):
        for _ in range(100):
            rate_limit.enforce(_request(tokens[0]))


def test_anonymous_traffic_is_bucketed_by_ip_with_a_limit_that_fits_an_office():
    for _ in range(settings.rate_limit_anonymous_per_minute):
        rate_limit.enforce(_request(""))
    with pytest.raises(RateLimited) as caught:
        rate_limit.enforce(_request(""))
    assert caught.value.extra["retry_after_seconds"] >= 1
    assert rate_limit.retry_after_seconds(caught.value) >= 1
    # Another address is unaffected.
    rate_limit.enforce(_request("", host="10.20.30.41"))


def test_one_account_cannot_be_guessed_at_while_the_office_still_signs_in():
    for account in range(40):
        rate_limit.enforce_login("10.20.30.40", f"user{account}@nile.example.com")
    with pytest.raises(RateLimited):
        for _ in range(settings.rate_limit_login_per_account + 1):
            rate_limit.enforce_login("10.20.30.40", "victim@nile.example.com")


def test_mass_attempts_from_one_host_are_still_capped():
    with pytest.raises(RateLimited):
        for index in range(settings.rate_limit_login_per_ip + 1):
            rate_limit.enforce_login("10.20.30.40", f"noise{index}@nile.example.com")


async def test_thirty_sign_ins_from_one_ip_succeed_over_http(client):
    """The HTTP-level regression: six colleagues each signing in five times from the same address."""
    accounts = ["admin@pilot.procureai", "requester@nile.example.com", "buyer@nile.example.com",
                "finance@nile.example.com", "manager@nile.example.com",
                "controller@nile.example.com"]
    for _ in range(5):
        for email in accounts:
            response = await client.post("/api/v1/auth/login",
                                         json={"email": email, "password": "Demo!Pilot2026"})
            assert response.status_code == 200, f"{email}: {response.status_code} {response.text}"


async def test_the_429_is_well_formed_and_carries_retry_after(client):
    previous = settings.rate_limit_anonymous_per_minute
    settings.rate_limit_anonymous_per_minute = 3
    try:
        for _ in range(3):
            assert (await client.get("/health")).status_code == 200
        blocked = await client.get("/health")
    finally:
        settings.rate_limit_anonymous_per_minute = previous
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.headers["x-ratelimit-scope"] == "identity"
