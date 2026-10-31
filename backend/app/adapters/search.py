"""Search / supplier discovery providers (official APIs first, public data only)."""
from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx

from app.adapters.base import SearchHit, SupplierCandidateData

CITIES = {
    "EG": ["Cairo", "Giza", "Alexandria", "Suez", "10th of Ramadan", "Sadat City", "6th of October", "Tanta"],
    "SA": ["Riyadh", "Jeddah", "Dammam", "Khobar"],
    "AE": ["Dubai", "Abu Dhabi", "Sharjah"],
}

ARABIC_SUFFIXES = ["للتجارة", "للصناعة", "للتوريدات", "ذ.م.م", "ش.م.م", "وشركاه"]
EN_SUFFIXES = ["Trading", "Industries", "Supplies", "Industrial Co.", "Group", "& Partners"]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


class FakeSearchProvider:
    """Deterministic supplier generator seeded from the query (no network, no invented prod data).

    Every candidate carries a synthetic `source_url` under the reserved `.example` TLD so it can
    never be mistaken for a real company; the discovery gate still requires buyer review.
    """

    name = "fake"

    async def search(self, query: str, *, limit: int = 10, country: str = "EG") -> list[SearchHit]:
        hits: list[SearchHit] = []
        for _i, cand in enumerate(await self.discover_suppliers(query, country=country, limit=limit)):
            hits.append(SearchHit(title=cand.name, url=cand.source_url,
                                  snippet=f"{cand.name} - supplier of {query} in {cand.city}", source="fake"))
        return hits

    async def discover_suppliers(self, query: str, *, country: str = "EG", limit: int = 24) -> list[SupplierCandidateData]:
        base = re.sub(r"\s+", " ", (query or "industrial supplies").strip())
        cities = CITIES.get(country, CITIES["EG"])
        results: list[SupplierCandidateData] = []
        seen: set[str] = set()
        for i in range(limit):
            digest = hashlib.sha256(f"{base}:{i}".encode()).hexdigest()
            city = cities[i % len(cities)]
            if i % 3 == 0:
                name = f"{city} {base.title()} " + EN_SUFFIXES[i % len(EN_SUFFIXES)]
                name_ar = f"{city} {base} " + ARABIC_SUFFIXES[i % len(ARABIC_SUFFIXES)]
            elif i % 3 == 1:
                name = f"Al {['Noor', 'Safwa', 'Fath', 'Etqan'][i % 4]} {base.title()} Trading"
                name_ar = f"مؤسسة ال{['نور', 'صفوة', 'فتح', 'إتقان'][i % 4]} لتجارة {base}"
            else:
                name = f"{base.title()} Solutions {['LLC', 'Co.', 'Group'][i % 3]}"
                name_ar = f"حلول {base} " + ARABIC_SUFFIXES[i % len(ARABIC_SUFFIXES)]
            key = _slug(name)
            if key in seen:
                continue
            seen.add(key)
            domain = f"{key or 'supplier'}-{digest[:6]}.example"
            confidence = round(0.72 + (int(digest[:2], 16) / 255) * 0.24, 3)
            results.append(SupplierCandidateData(
                name=name,
                name_ar=name_ar,
                website=f"https://{domain}",
                domain=domain,
                country=country,
                city=city,
                email=f"sales@{domain}",
                phone=f"+20 1{int(digest[:2], 16) % 3} {digest[2:5]} {digest[5:8]} {digest[8:11]}",
                source_url=f"https://search.example/results?q={query.replace(' ', '+')}&id={digest[:8]}",
                confidence=min(confidence, 0.97),
                evidence={
                    "matched_query": query,
                    "snippet": f"Supplier of {base} in {city}, {country}",
                    "provider": "fake",
                    "domain_age_hint": "unknown",
                },
            ))
        return results


SERPAPI_BASE = "https://serpapi.com"
BRAVE_BASE = "https://api.search.brave.com"


class SerpApiSearchProvider:
    name = "serpapi"

    def __init__(self, api_key: str, crawler: PageFetcher | None = None,
                 base_url: str = SERPAPI_BASE) -> None:
        self.api_key = api_key
        self.crawler = crawler or PageFetcher()
        # A base URL is what lets an operator point at a proxy/regional gateway - and what lets the
        # test suite prove the real client over a real socket instead of patching it.
        self.base_url = base_url.rstrip("/")

    async def search(self, query: str, *, limit: int = 10, country: str = "EG") -> list[SearchHit]:
        params = {"engine": "google", "q": query, "api_key": self.api_key, "num": min(limit, 20),
                  "gl": country.lower(), "hl": "ar" if country == "EG" else "en"}
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.get(f"{self.base_url}/search.json", params=params)
        data = resp.json()
        return [
            SearchHit(title=item.get("title", ""), url=item.get("link", ""),
                      snippet=item.get("snippet", ""), source="serpapi")
            for item in data.get("organic_results", [])[:limit]
        ]

    async def discover_suppliers(self, query: str, *, country: str = "EG",
                                 limit: int = 24) -> list[SupplierCandidateData]:
        hits = await self.search(f"{query} supplier {country} contact email", limit=limit, country=country)
        candidates: list[SupplierCandidateData] = []
        for hit in hits:
            domain = re.sub(r"^https?://(www\.)?", "", hit.url).split("/")[0]
            page = await self.crawler.fetch(hit.url) if self.crawler else ""
            email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", page or hit.snippet)
            phone_match = re.search(r"\+?\d[\d\s\-()]{8,}\d", page or hit.snippet)
            candidates.append(SupplierCandidateData(
                name=hit.title.split("|")[0].split("-")[0].strip()[:200] or domain,
                website=f"https://{domain}",
                domain=domain,
                country=country,
                email=(email_match.group(0) if email_match else ""),
                phone=(phone_match.group(0) if phone_match else ""),
                source_url=hit.url,
                confidence=0.6 + (0.15 if email_match else 0) + (0.1 if phone_match else 0),
                evidence={"snippet": hit.snippet[:400], "provider": "serpapi"},
            ))
        return candidates


class BraveSearchProvider:
    name = "brave"

    def __init__(self, api_key: str, base_url: str = BRAVE_BASE) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def search(self, query: str, *, limit: int = 10, country: str = "EG") -> list[SearchHit]:
        headers = {"X-Subscription-Token": self.api_key, "Accept": "application/json"}
        params = {"q": query, "count": min(limit, 20), "country": country}
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.get(f"{self.base_url}/res/v1/web/search",
                                    headers=headers, params=params)
        data = resp.json()
        return [
            SearchHit(title=item.get("title", ""), url=item.get("url", ""),
                      snippet=item.get("description", ""), source="brave")
            for item in data.get("web", {}).get("results", [])[:limit]
        ]

    async def discover_suppliers(self, query: str, *, country: str = "EG",
                                 limit: int = 24) -> list[SupplierCandidateData]:
        return await SerpApiSearchProvider(self.api_key, base_url=self.base_url).discover_suppliers(
            query, country=country, limit=limit
        )


class PageFetcher:
    """SSRF-safe public page fetcher (blocks private ranges, respects robots.txt)."""

    PRIVATE_PREFIXES = (
        "10.", "127.", "169.254.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.",
        "172.31.", "192.168.", "0.", "100.64.", "::1", "fc", "fd",
    )
    USER_AGENT = "ProcureAI-DiscoveryBot/1.0 (+https://mantiq.tech/bot; contact=procurement@mantiq.tech)"

    def __init__(self, timeout: int = 15, max_bytes: int = 1_500_000) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes

    def is_public_url(self, url: str) -> bool:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = (parsed.hostname or "").lower()
        if not host or host in {"localhost", "metadata.google.internal"}:
            return False
        if any(host.startswith(prefix) for prefix in self.PRIVATE_PREFIXES):
            return False
        if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
            return False
        return True

    def robots_allows(self, url: str, robots_txt: str) -> bool:
        from urllib.parse import urlparse

        if not robots_txt:
            return True
        path = urlparse(url).path or "/"
        applies = False
        for line in robots_txt.splitlines():
            line = line.strip().lower()
            if line.startswith("user-agent:"):
                agent = line.split(":", 1)[1].strip()
                applies = agent in {"*", "procureai-discoverybot"}
            elif applies and line.startswith("disallow:"):
                rule = line.split(":", 1)[1].strip()
                if rule and path.startswith(rule):
                    return False
        return True

    async def fetch(self, url: str) -> str:  # pragma: no cover - network dependent
        from urllib.parse import urlparse

        if not self.is_public_url(url):
            return ""
        parsed = urlparse(url)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True,
                                         headers={"User-Agent": self.USER_AGENT}) as client:
                robots = ""
                try:
                    robots_resp = await client.get(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
                    robots = robots_resp.text if robots_resp.status_code == 200 else ""
                except Exception:
                    robots = ""
                if not self.robots_allows(url, robots):
                    return ""
                resp = await client.get(url)
                if resp.status_code >= 400:
                    return ""
                text = resp.text[: self.max_bytes]
                return re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
        except Exception:
            return ""


def build_search_provider(kind: str, api_key: str, base_url: str = "") -> Any:
    if kind == "serpapi" and api_key:
        return SerpApiSearchProvider(api_key, base_url=base_url or SERPAPI_BASE)
    if kind == "brave" and api_key:
        return BraveSearchProvider(api_key, base_url=base_url or BRAVE_BASE)
    return FakeSearchProvider()
