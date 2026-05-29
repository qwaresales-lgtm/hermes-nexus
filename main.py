import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.config import get_settings
from core.logger import setup_logging
from linear.router import router as linear_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("Hermes Nexus API starting")
    yield
    logging.getLogger(__name__).info("Hermes Nexus API shutting down")


app = FastAPI(
    title="Hermes Nexus API",
    description=(
        "AI 多代理人任務調動系統的 API 層。\n\n"
        "**Linear** 端點提供 Linear 完整 CRUD 操作，供各子 Agent 直接呼叫。\n\n"
        "每個請求請帶上 `X-Agent-Name` header，API 會將呼叫方記錄進 log，"
        "comment 也會自動附上呼叫方與時間戳記。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(linear_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    caller = request.headers.get("X-Agent-Name", "unknown")
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    logging.getLogger("http").info(
        f"[{caller}] {request.method} {request.url.path} → {response.status_code} ({duration:.3f}s)"
    )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    caller = request.headers.get("X-Agent-Name", "unknown")
    logging.getLogger("error").error(
        f"[{caller}] Unhandled error on {request.url.path}: {exc}", exc_info=True
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health", tags=["System"], summary="Health check")
def health():
    return {"status": "ok"}


# MCP — 所有 FastAPI 端點自動轉為 MCP tools（連線位置：/mcp）
from fastapi_mcp import FastApiMCP  # noqa: E402

mcp = FastApiMCP(app)
mcp.mount()
