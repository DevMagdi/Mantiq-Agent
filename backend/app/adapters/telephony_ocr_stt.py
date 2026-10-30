"""Telephony (AI voice calls), OCR and STT adapters with deterministic Fakes."""
from __future__ import annotations

import asyncio
import base64
import io
import re
from typing import Any

from app.adapters.base import CallResult, OcrResult, TranscriptResult, fake_id


class FakeTelephonyProvider:
    """Simulates an Egyptian-Arabic supplier call: disclosure -> consent -> quote."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: dict[str, CallResult] = {}

    async def place_call(self, *, to: str, script: str, disclosure: str, country: str,
                         language: str, consent_required: bool) -> CallResult:
        number_hint = sum(ord(c) for c in to) % 7
        if number_hint == 0:
            result = CallResult(provider_call_id=fake_id("call", to), status="no_answer",
                                disclosure_played=True, consent_given=False, duration_seconds=12,
                                summary="No answer after 12 seconds; will retry per cadence.")
        else:
            unit_price = 21500 + (sum(ord(c) for c in to) % 5) * 750
            quantity = 50
            # Deterministic "hard question" so the policy path is exercised without a live line:
            # 0 = none, 1 = competitor pricing, 2 = a commitment request, 3 = a spec change.
            hard_question = {
                0: "",
                1: "[Supplier] طيب أسعار المنافسين عندهم كام؟ عايز أعرف سعر السوق.\n",
                2: "[Supplier] يعني الطلب رسّي علينا ونوقّع؟ عايز تأكيد.\n",
                3: "[Supplier] نقدر نغيّر المواصفات ونوّرد قياس مختلف؟\n",
            }[sum(ord(c) for c in to) % 4]
            transcript = (
                f"[AI] {disclosure}\n"
                f"[Supplier] أيوة موافق، اتفضل.\n"
                f"{hard_question}"
                f"[AI] بنسأل عن عرض سعر لكمية {quantity} طن حديد تسليح 12 مم تسليم خلال 3 أسابيع.\n"
                f"[Supplier] السعر {unit_price:,} جنيه للطن، التسليم خلال 21 يوم، صلاحية العرض 14 يوم.\n"
                f"[AI] شكراً جزيلاً، هنبعتلك تأكيد مكتوب على الواتساب.\n"
            )
            result = CallResult(
                provider_call_id=fake_id("call", to), status="completed", disclosure_played=True,
                consent_given=True, transcript=transcript, duration_seconds=96,
                recording_uri=f"fake://recordings/{fake_id('rec', to)}.mp3",
                summary="Supplier quoted a unit price with 21-day lead time and 14-day validity.",
            )
        self.calls[result.provider_call_id] = result
        return result

    async def fetch_call(self, provider_call_id: str) -> CallResult:
        return self.calls.get(provider_call_id, CallResult(provider_call_id=provider_call_id, status="unknown"))


class TwilioTelephonyProvider:
    """Twilio Programmable Voice (outbound) with recording + speech gather."""

    name = "twilio"

    def __init__(self, sid: str, token: str, from_number: str) -> None:
        self.sid = sid
        self.token = token
        self.from_number = from_number

    async def place_call(self, *, to: str, script: str, disclosure: str, country: str,
                         language: str, consent_required: bool) -> CallResult:  # pragma: no cover - network
        import httpx

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls.json"
        twiml = (
            f"<Response><Say language='ar-EG'>{disclosure}</Say>"
            "<Gather input='speech' speechTimeout='auto' language='ar-EG'>"
            f"<Say language='ar-EG'>{script[:600]}</Say></Gather>"
            "<Record maxLength='480' playBeep='true'/></Response>"
        )
        try:
            async with httpx.AsyncClient(timeout=30, auth=(self.sid, self.token)) as client:
                resp = await client.post(url, data={"To": to, "From": self.from_number, "Twiml": twiml})
            data = resp.json()
            return CallResult(provider_call_id=data.get("sid", ""), status="queued")
        except Exception as exc:
            return CallResult(provider_call_id="", status="failed", summary=str(exc)[:200])

    async def fetch_call(self, provider_call_id: str) -> CallResult:  # pragma: no cover - network
        import httpx

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls/{provider_call_id}.json"
        async with httpx.AsyncClient(timeout=20, auth=(self.sid, self.token)) as client:
            resp = await client.get(url)
        data = resp.json()
        return CallResult(provider_call_id=provider_call_id, status=data.get("status", "unknown"),
                          duration_seconds=int(data.get("duration") or 0))


class FakeOcrProvider:
    """Returns the supplied fixture text (or an embedded-text extraction)."""

    name = "fake"

    async def extract(self, *, data: bytes, filename: str, languages: str = "ara+eng") -> OcrResult:
        try:
            text = data.decode("utf-8", "ignore")
        except Exception:
            text = ""
        if not text.strip() or filename.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
            text = "طلب شراء / Purchase request\n500 طن حديد تسليح 12مم\nالتسليم خلال 3 أسابيع"
        return OcrResult(text=text, confidence=0.93, blocks=[{"text": text, "page": 1}])


class TesseractOcrProvider:
    """Tesseract with Arabic + English traineddata (container image installs tesseract-ocr)."""

    name = "tesseract"

    def __init__(self, languages: str = "ara+eng") -> None:
        self.languages = languages

    async def extract(self, *, data: bytes, filename: str, languages: str = "ara+eng") -> OcrResult:
        def _run() -> OcrResult:
            try:
                import pytesseract  # type: ignore
                from PIL import Image  # type: ignore
            except Exception:
                return OcrResult(text="", confidence=0.0)
            try:
                image = Image.open(io.BytesIO(data))
                text = pytesseract.image_to_string(image, lang=languages or self.languages)
                return OcrResult(text=text, confidence=0.85)
            except Exception:
                return OcrResult(text="", confidence=0.0)

        return await asyncio.to_thread(_run)


class FakeSttProvider:
    name = "fake"

    def __init__(self, script: str = "") -> None:
        self.script = script

    async def transcribe(self, *, data: bytes, filename: str, language: str = "ar") -> TranscriptResult:
        text = self.script or "محتاج ٥٠٠ طن حديد تسليح ١٢ مم والتسليم بعد ٣ أسابيع"
        return TranscriptResult(text=text, language="ar-EG", confidence=0.91)


class WhisperSttProvider:
    """Whisper-compatible HTTP endpoint (self-hosted faster-whisper / OpenAI-compatible)."""

    name = "whisper"

    def __init__(self, base_url: str, api_key: str = "", model: str = "whisper-1") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def transcribe(self, *, data: bytes, filename: str, language: str = "ar") -> TranscriptResult:
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        files = {"file": (filename or "audio.webm", data, "application/octet-stream")}
        payload: dict[str, Any] = {"model": self.model, "language": language}
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{self.base_url}/audio/transcriptions", headers=headers,
                                         files=files, data=payload)
            data_json = resp.json()
            return TranscriptResult(text=data_json.get("text", ""), language=language, confidence=0.9)
        except Exception:
            return TranscriptResult(text="", language=language, confidence=0.0)


def base64_data(uri: str) -> bytes:
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    return b""


def extract_numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d,.]*", text or "")
