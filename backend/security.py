import secrets
from typing import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

HEADER_NAME = "x-access-key"


class AccessKeyMiddleware:
    """Reject every request that does not carry the right X-Access-Key header.
    """

    def __init__(self, app: ASGIApp, access_key: str, public_paths: Iterable[str] = ("/health",)) -> None:
        self.app = app
        self.expected = access_key.encode("utf-8")
        self.public_paths = frozenset(public_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.public_paths:
            await self.app(scope, receive, send)
            return

        provided = Headers(scope=scope).get(HEADER_NAME)
        if not provided:
            detail = "Missing access key. Send it in the X-Access-Key header."
        elif not secrets.compare_digest(provided.encode("utf-8"), self.expected):
            detail = "Invalid access key."
        else:
            await self.app(scope, receive, send)
            return

        await JSONResponse({"detail": detail}, status_code=401)(scope, receive, send)