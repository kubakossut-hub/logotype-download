import asyncio
import base64
import httpx
from services.quality import assess_image, _unreachable

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/*,*/*;q=0.8",
}

SEM = asyncio.Semaphore(12)
_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=5)


def _decode_data_uri(url: str) -> bytes | None:
    """Decode a data: URI to raw bytes."""
    try:
        # data:[<mediatype>][;base64],<data>
        _, rest = url.split(",", 1)
        header = url.split(",")[0]
        if ";base64" in header:
            return base64.b64decode(rest)
        return rest.encode()
    except Exception:
        return None


async def fetch_and_assess(client: httpx.AsyncClient, candidate_id: str, company: str, url: str) -> dict:
    async with SEM:
        if url.startswith("data:"):
            content = _decode_data_uri(url)
            result = assess_image(content, url) if content else _unreachable()
        elif not url:
            result = _unreachable()
        else:
            try:
                resp = await client.get(url, timeout=8.0, follow_redirects=True)
                if resp.status_code != 200:
                    result = _unreachable()
                elif "text/html" in resp.headers.get("content-type", ""):
                    result = _unreachable()
                else:
                    result = assess_image(resp.content, url)
            except Exception:
                result = _unreachable()

    return {"candidate_id": candidate_id, "company": company, "url": url, **result}


async def assess_all(items: list[dict]) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, limits=_LIMITS) as client:
        tasks = [
            fetch_and_assess(client, item["candidate_id"], item["company"], item["url"])
            for item in items
        ]
        return await asyncio.gather(*tasks)


async def fetch_bytes(url: str) -> bytes | None:
    if url.startswith("data:"):
        return _decode_data_uri(url)
    async with httpx.AsyncClient(headers=HEADERS) as client:
        try:
            resp = await client.get(url, timeout=10.0, follow_redirects=True)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            pass
    return None
