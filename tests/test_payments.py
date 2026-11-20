"""Buying credits through Asaas (faked): credits are added only by Asaas's webhook, once."""
from tests.test_web import make_client, register

TOKEN = "webhook-token-for-tests"


class FakeAsaas:
    """Records the checkouts asked for; their ids are chk-1, chk-2, ..."""

    def __init__(self, fail=False):
        self.checkouts, self.fail = [], fail

    def __call__(self, base_url, api_key, reference, credits, price_cents, return_url):
        if self.fail:
            raise ValueError("Asaas refused the payment (400: invalid value).")
        self.checkouts.append({"base_url": base_url, "api_key": api_key, "reference": reference, "credits": credits,
                               "price_cents": price_cents, "return_url": return_url})
        n = len(self.checkouts)
        return f"chk-{n}", f"https://sandbox.asaas.com/checkoutSession/show?id=chk-{n}"


def client_with(tmp_path, asaas, **settings):
    return make_client(tmp_path, create_checkout=asaas, asaas_api_key="$aact_hmlg_test", asaas_sandbox=True,
                       asaas_webhook_token=TOKEN, credit_price_cents=150, credit_packs=[10, 30], **settings)


def notify(client, event, token=TOKEN, **objects):
    headers = {"asaas-access-token": token} if token is not None else {}
    return client.post("/api/payments/webhook", json={"id": f"evt_{event}", "event": event, **objects},
                       headers=headers)


def balance(client) -> int:
    return client.get("/api/credits").json()["balance"]


def test_buying_credits(tmp_path):
    asaas = FakeAsaas()
    with client_with(tmp_path, asaas) as client:
        register(client)
        assert client.get("/api/site").json()["payments"] == {
            "configured": True, "sandbox": True, "price_cents": 150, "min": 10, "max": 1000, "suggested": [10, 30]}
        for wrong in (9, 1001, 0, -5):  # any number from the minimum to the maximum, nothing else
            refused = client.post("/api/payments", json={"credits": wrong})
            assert refused.status_code == 422 and refused.json()["detail"] == "Choose between 10 and 1000 credits."
        assert client.post("/api/payments", json={"credits": "lots"}).status_code == 422
        assert asaas.checkouts == []

        bought = client.post("/api/payments", json={"credits": 30})
        assert bought.status_code == 201, bought.text
        purchase = bought.json()
        assert purchase["link"].startswith("https://sandbox.asaas.com/")
        assert asaas.checkouts == [{"base_url": "https://api-sandbox.asaas.com/v3", "api_key": "$aact_hmlg_test",
                                    "reference": purchase["id"], "credits": 30, "price_cents": 4500,
                                    "return_url": f"http://testserver/credits/{purchase['id']}"}]
        shown = client.get(f"/api/payments/{purchase['id']}").json()
        assert shown["status"] == "pending" and balance(client) == 10  # coming back from Asaas proves nothing

        # Only Asaas, with the token, can say it was paid.
        paid = {"checkout": {"id": "chk-1", "status": "PAID"}}
        assert notify(client, "CHECKOUT_PAID", token=None, **paid).status_code == 401
        assert notify(client, "CHECKOUT_PAID", token="guess", **paid).status_code == 401
        assert balance(client) == 10
        assert notify(client, "CHECKOUT_PAID", **paid).status_code == 200
        assert balance(client) == 40
        assert notify(client, "CHECKOUT_PAID", **paid).status_code == 200  # sent again: added once
        assert balance(client) == 40
        shown = client.get(f"/api/payments/{purchase['id']}").json()
        assert shown["status"] == "paid" and shown["paid_at"]
        entry = client.get("/api/credits").json()["entries"][0]
        assert (entry["kind"], entry["amount"], entry["note"]) == ("purchase", 30, "Bought 30 credits")
        assert [p["id"] for p in client.get("/api/payments").json()] == [purchase["id"]]

        # Any amount in between, not only the shortcuts; a payment found by our id alone (externalReference) counts.
        second = client.post("/api/payments", json={"credits": 17}).json()
        assert asaas.checkouts[-1]["price_cents"] == 2550
        notify(client, "PAYMENT_RECEIVED", payment={"id": "pay_1", "externalReference": second["id"]})
        assert balance(client) == 57

        # Expired checkouts add nothing; events about other things are acknowledged, so Asaas doesn't resend them.
        third = client.post("/api/payments", json={"credits": 10}).json()
        notify(client, "CHECKOUT_EXPIRED", checkout={"id": "chk-3"})
        assert client.get(f"/api/payments/{third['id']}").json()["status"] == "expired" and balance(client) == 57
        assert notify(client, "CHECKOUT_PAID", checkout={"id": "chk-unknown"}).status_code == 200
        assert notify(client, "PAYMENT_CREATED", payment={"id": "pay_2"}).status_code == 200

        # Purchases are private.
        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.get(f"/api/payments/{purchase['id']}").status_code == 404
        assert client.get("/api/payments").json() == []
        export = client.get("/api/account/export").json()
        assert export["purchases"] == []


def test_failed_checkout_keeps_nothing(tmp_path):
    with client_with(tmp_path, FakeAsaas(fail=True)) as client:
        register(client)
        refused = client.post("/api/payments", json={"credits": 10})
        assert refused.status_code == 502 and "invalid value" in refused.json()["detail"]
        assert client.get("/api/payments").json() == []


def test_buying_needs_asaas(tmp_path):
    with make_client(tmp_path) as client:
        register(client)
        assert client.get("/api/site").json()["payments"]["configured"] is False
        assert client.post("/api/payments", json={"credits": 10}).status_code == 409
        # Without a token set, no webhook is believed.
        assert notify(client, "CHECKOUT_PAID", token="", checkout={"id": "x"}).status_code == 401


def test_asaas_checkout_request(monkeypatch):
    """What is sent to Asaas's API, and how its errors are shown."""
    import httpx
    import pytest
    from web import asaas

    sent = {}

    def post(url, json, timeout, headers):
        sent.update(url=url, body=json, headers=headers)
        if json["items"][0]["value"] > 1000:
            return httpx.Response(400, json={"errors": [{"code": "invalid_value", "description": "Valor inválido"}]})
        return httpx.Response(200, json={"id": "chk-9", "link": "https://asaas.com/c/chk-9", "status": "ACTIVE"})

    monkeypatch.setattr(asaas.httpx, "post", post)
    assert asaas.create_checkout("https://api-sandbox.asaas.com/v3", "key", "p1", 30, 4500,
                                 "https://pidi.example.com/credits/p1") == ("chk-9", "https://asaas.com/c/chk-9")
    body = sent["body"]
    assert sent["url"] == "https://api-sandbox.asaas.com/v3/checkouts" and sent["headers"]["access_token"] == "key"
    assert body["billingTypes"] == ["PIX", "CREDIT_CARD"] and body["chargeTypes"] == ["DETACHED"]
    assert body["externalReference"] == "p1"
    assert body["callback"]["successUrl"] == "https://pidi.example.com/credits/p1?result=success"
    item = body["items"][0]
    assert item["value"] == 45.0 and item["quantity"] == 1 and len(item["name"]) <= 30 and item["imageBase64"]
    with pytest.raises(ValueError, match="400: Valor inválido"):
        asaas.create_checkout("u", "key", "p2", 1000, 150000, "r")
