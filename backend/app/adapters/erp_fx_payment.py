"""ERP connectors (Odoo + file exchange + typed stubs), FX and payment gateways."""
from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from app.adapters.base import ErpResult, FxQuote, PaymentResult


# ------------------------------------------------------------------ ERP
class FileExchangeConnector:
    """Generic ERP file exchange: import PR CSV from a watched folder/S3 prefix, export PO CSV."""

    name = "file"

    def __init__(self, inbox_dir: str = "var/erp/inbox", outbox_dir: str = "var/erp/outbox") -> None:
        self.inbox = Path(inbox_dir)
        self.outbox = Path(outbox_dir)

    async def test_connection(self) -> ErpResult:
        try:
            self.inbox.mkdir(parents=True, exist_ok=True)
            self.outbox.mkdir(parents=True, exist_ok=True)
            return ErpResult(ok=True, reference=str(self.inbox))
        except Exception as exc:
            return ErpResult(ok=False, error=str(exc)[:200])

    async def import_requisitions(self, limit: int = 20) -> list[dict[str, Any]]:
        self.inbox.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        for path in sorted(self.inbox.glob("*.csv"))[:limit]:
            with path.open(newline="", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    rows.append({**row, "_source_file": path.name})
            path.rename(path.with_suffix(".csv.imported"))
        return rows

    async def export_purchase_order(self, po: dict[str, Any]) -> ErpResult:
        self.outbox.mkdir(parents=True, exist_ok=True)
        path = self.outbox / f"PO-{po.get('code', 'draft')}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["sku", "description", "quantity", "uom", "unit_price", "currency"])
            for line in po.get("lines", []):
                writer.writerow([line.get("sku", ""), line.get("description", ""), line.get("quantity", ""),
                                 line.get("uom", ""), line.get("unit_price", ""), po.get("currency", "")])
        return ErpResult(ok=True, reference=str(path), payload={"file": path.name})


class OdooConnector:
    """Odoo JSON-RPC connector (imports purchase.order lines, exports POs)."""

    name = "odoo"

    def __init__(self, url: str, db: str, username: str, api_key: str) -> None:
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.api_key = api_key
        self.uid: int | None = None

    async def _rpc(self, service: str, method: str, args: list[Any]) -> Any:  # pragma: no cover - network
        payload = {
            "jsonrpc": "2.0", "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": 1,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.url}/jsonrpc", json=payload)
        data = resp.json()
        if "error" in data:
            raise RuntimeError(json.dumps(data["error"])[:300])
        return data.get("result")

    async def _ensure_uid(self) -> int:  # pragma: no cover - network
        if self.uid is None:
            self.uid = int(await self._rpc("common", "login", [self.db, self.username, self.api_key]))
        return self.uid

    async def test_connection(self) -> ErpResult:
        try:
            version = await self._rpc("common", "version", [])
            return ErpResult(ok=True, reference=str(version.get("server_version", "")), payload=version)
        except Exception as exc:
            return ErpResult(ok=False, error=str(exc)[:200])

    async def import_requisitions(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            uid = await self._ensure_uid()
            records = await self._rpc("object", "execute_kw", [
                self.db, uid, self.api_key, "purchase.order", "search_read",
                [[["state", "=", "draft"]]], {"fields": ["name", "partner_id", "amount_total", "currency_id"],
                                              "limit": limit},
            ])
            return [{
                "code": rec.get("name"), "supplier": (rec.get("partner_id") or [None, ""])[1],
                "amount": rec.get("amount_total"), "currency": (rec.get("currency_id") or [None, "EGP"])[1],
                "source": "odoo",
            } for rec in records or []]
        except Exception:
            return []

    async def export_purchase_order(self, po: dict[str, Any]) -> ErpResult:
        try:
            uid = await self._ensure_uid()
            order_id = await self._rpc("object", "execute_kw", [
                self.db, uid, self.api_key, "purchase.order", "create", [{
                    "partner_ref": po.get("code"),
                    "origin": po.get("rfq_code", ""),
                    "order_line": [(0, 0, {
                        "name": line.get("description", ""),
                        "product_qty": float(line.get("quantity") or 0),
                        "price_unit": float(line.get("unit_price") or 0),
                    }) for line in po.get("lines", [])],
                }],
            ])
            return ErpResult(ok=True, reference=str(order_id))
        except Exception as exc:
            return ErpResult(ok=False, error=str(exc)[:200])


class TypedErpStub:
    """Documented mapping stub for ERP systems scheduled for V1.5 (SAP / Oracle / Dynamics)."""

    def __init__(self, name: str, mapping_doc: str) -> None:
        self.name = name
        self.mapping_doc = mapping_doc

    async def test_connection(self) -> ErpResult:
        return ErpResult(ok=False, error=f"{self.name} connector is a V1.5 adapter stub. See docs/ARCHITECTURE.md")

    async def import_requisitions(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    async def export_purchase_order(self, po: dict[str, Any]) -> ErpResult:
        return ErpResult(ok=False, error=f"{self.name} export not enabled (V1.5). PO available as PDF/Excel.")


SAP_STUB = TypedErpStub(
    "sap",
    "SAP S/4HANA: PR -> API_PURCHASEREQUISITION_2 (OData). PO -> API_PURCHASEORDER_PROCESS_SRV. "
    "Auth: OAuth2 client credentials (SAP BTP destination).",
)
ORACLE_STUB = TypedErpStub(
    "oracle",
    "Oracle Fusion: Requisitions REST /fscmRestApi/resources/11.13.18.05/requisitions; PO via "
    "purchaseOrders resource. Auth: basic/OAuth2.",
)
DYNAMICS_STUB = TypedErpStub(
    "dynamics",
    "Dynamics 365 F&O: OData /data/PurchaseRequisitionHeadersV2; PO via PurchaseOrderHeadersV2. "
    "Auth: Azure AD client credentials.",
)


# ------------------------------------------------------------------ FX
class StaticFxProvider:
    """Deterministic FX table for dev/tests (dated; not a market feed)."""

    name = "static"
    # base USD -> quote
    RATES: dict[str, str] = {
        "USD": "1", "EGP": "48.55", "SAR": "3.75", "AED": "3.6725", "QAR": "3.64", "KWD": "0.3067",
        "EUR": "0.92", "GBP": "0.78", "CNY": "7.12", "TRY": "34.10", "INR": "83.55",
    }

    async def rate(self, base: str, quote: str) -> FxQuote | None:
        base = (base or "USD").upper()
        quote = (quote or "USD").upper()
        if base not in self.RATES or quote not in self.RATES:
            return None
        rate = Decimal(self.RATES[quote]) / Decimal(self.RATES[base])
        return FxQuote(base=base, quote=quote, rate=str(rate.quantize(Decimal("0.0000000001"))),
                       as_of=datetime.now(UTC).replace(microsecond=0).isoformat(), source="static")


class HttpFxProvider:
    """exchangerate.host style JSON feed; cached in `fx_rate` by the FX service."""

    name = "http"

    def __init__(self, base_url: str = "https://api.exchangerate.host", api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def rate(self, base: str, quote: str) -> FxQuote | None:  # pragma: no cover - network
        params = {"base": base, "symbols": quote}
        if self.api_key:
            params["access_key"] = self.api_key
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.base_url}/latest", params=params)
            data = resp.json()
            rate = data.get("rates", {}).get(quote)
            if rate is None:
                return None
            return FxQuote(base=base, quote=quote, rate=str(rate), as_of=data.get("date", ""), source="http")
        except Exception:
            return None


# ------------------------------------------------------------------ payments
class FakePaymentGateway:
    name = "fake"

    def __init__(self) -> None:
        self.invoices: dict[str, PaymentResult] = {}

    async def create_invoice(self, *, amount: str, currency: str, reference: str,
                             description: str) -> PaymentResult:
        result = PaymentResult(ok=True, reference=f"fake_inv_{reference}", status="paid",
                              url=f"https://pay.example/{reference}")
        self.invoices[reference] = result
        return result

    async def verify_webhook(self, body: bytes, signature: str) -> bool:
        import hashlib
        import hmac

        return hmac.compare_digest(hashlib.sha256(body).hexdigest(), signature or "")


class StripeGateway:
    name = "stripe"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def create_invoice(self, *, amount: str, currency: str, reference: str,
                             description: str) -> PaymentResult:  # pragma: no cover - network
        try:
            async with httpx.AsyncClient(timeout=25, auth=(self.api_key, "")) as client:
                resp = await client.post("https://api.stripe.com/v1/payment_intents", data={
                    "amount": str(int(Decimal(amount) * 100)),
                    "currency": currency.lower(),
                    "description": description,
                    "metadata[reference]": reference,
                    "automatic_payment_methods[enabled]": "true",
                })
            data = resp.json()
            if resp.status_code >= 400:
                return PaymentResult(ok=False, error=data.get("error", {}).get("message", "stripe error"))
            return PaymentResult(ok=True, reference=data.get("id", ""), status=data.get("status", "requires_payment"),
                                 url=data.get("next_action", {}).get("redirect_to_url", {}).get("url", ""))
        except Exception as exc:
            return PaymentResult(ok=False, error=str(exc)[:200])

    async def verify_webhook(self, body: bytes, signature: str) -> bool:  # pragma: no cover - network
        import hashlib
        import hmac

        return hmac.compare_digest(
            hmac.new(self.api_key.encode(), body, hashlib.sha256).hexdigest(), signature or ""
        )


class PaymobGateway:
    """Egyptian payment gateway skeleton (V1.5): accepts iframe key + HMAC verification."""

    name = "paymob"

    def __init__(self, api_key: str = "", integration_id: str = "", hmac_secret: str = "") -> None:
        self.api_key = api_key
        self.integration_id = integration_id
        self.hmac_secret = hmac_secret

    async def create_invoice(self, *, amount: str, currency: str, reference: str,
                             description: str) -> PaymentResult:
        return PaymentResult(ok=False, error="Paymob adapter is a V1.5 extension point; configure API key + HMAC.")

    async def verify_webhook(self, body: bytes, signature: str) -> bool:
        import hashlib
        import hmac

        return hmac.compare_digest(
            hmac.new(self.hmac_secret.encode(), body, hashlib.sha256).hexdigest(), signature or ""
        )


# ------------------------------------------------------------------ storage
class LocalStorage:
    """Filesystem object storage (dev/on-premise). S3 adapter shares the same interface."""

    name = "local"

    def __init__(self, root: str = "var/storage") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def write_text(self, key: str, text: str) -> str:
        return self.put(key, text.encode())


class S3Storage:
    """S3-compatible (MinIO/AWS) storage via boto3 when installed."""

    name = "s3"

    def __init__(self, bucket: str, endpoint: str = "", access_key: str = "", secret_key: str = "") -> None:
        self.bucket = bucket
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key

    def _client(self):  # pragma: no cover - optional dependency
        import boto3  # type: ignore

        return boto3.client("s3", endpoint_url=self.endpoint or None,
                            aws_access_key_id=self.access_key or None,
                            aws_secret_access_key=self.secret_key or None)

    def put(self, key: str, data: bytes) -> str:  # pragma: no cover
        self._client().put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get(self, key: str) -> bytes:  # pragma: no cover
        return self._client().get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:  # pragma: no cover
        try:
            self._client().head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


def csv_bytes(rows: list[dict[str, Any]], columns: list[str] | None = None) -> bytes:
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=columns or list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")
