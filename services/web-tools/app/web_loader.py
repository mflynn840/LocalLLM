import hmac
import aiohttp
import os
import logging

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.web import fetch_page

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web-loader")
app = FastAPI(title="Local Web Loader", docs_url=None, redoc_url=None)


class LoadRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)


@app.post("/load")
async def load_pages(
    request: LoadRequest,
    authorization: str | None = Header(default=None),
) -> list[dict[str, object]]:
    expected_key = os.environ.get("WEB_LOADER_API_KEY", "")
    supplied_key = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected_key or not hmac.compare_digest(supplied_key, expected_key):
        raise HTTPException(status_code=401, detail="Invalid web-loader credentials.")

    loaded: list[dict[str, object]] = []
    for url in request.urls:
        try:
            page = await fetch_page(url)
        except (aiohttp.ClientError, OSError, ValueError, TimeoutError) as exc:
            logger.warning("Could not load requested web page: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        loaded.append(
            {
                "page_content": page["text"],
                "metadata": {"source": url, "title": page["title"]},
            }
        )
    return loaded
