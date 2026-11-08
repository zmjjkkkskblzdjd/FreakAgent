from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from . import parser, sse
from .config import config
from .schemas import (
    CapabilitiesResponse,
    ErrorBody,
    ErrorCode,
    FormatsResponse,
    HealthResponse,
    ParseOptions,
    TaskCreatedResponse,
    TaskResponse,
)
from .task_manager import (
    NotCancellableError,
    QueueFullError,
    TaskGoneError,
    TaskNotFoundError,
    task_manager,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("doc_parser")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("warming up docling (default pipeline)...")
    await asyncio.to_thread(parser.warmup)
    log.info("docling ready")
    task_manager.start()
    yield
    await task_manager.stop()


app = FastAPI(title="doc_parser", version="0.1.0", lifespan=lifespan)



def _error(status_code: int, code: ErrorCode, message: str,
           details: dict | None = None, trace_id: str | None = None) -> JSONResponse:
    body = ErrorBody(code=code, message=message, details=details or {})
    if trace_id:
        body.trace_id = trace_id
    return JSONResponse(status_code=status_code, content={"error": body.model_dump(mode="json")})


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    request.state.trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["x-trace-id"] = request.state.trace_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("unhandled error trace=%s", getattr(request.state, "trace_id", "-"))
    return _error(500, ErrorCode.INTERNAL_ERROR, "内部错误",
                  trace_id=getattr(request.state, "trace_id", None))



@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        model_loaded=parser.model_loaded(),
        queue_size=task_manager.queue_size,
        running_tasks=task_manager.running_tasks,
        uptime_s=round(task_manager.uptime_s, 1),
    )


@app.get("/v1/formats", response_model=FormatsResponse)
async def formats() -> FormatsResponse:
    return FormatsResponse(
        formats=sorted(config.SUPPORTED_EXTENSIONS),
        max_file_size=config.MAX_FILE_SIZE,
        max_files_per_task=config.MAX_FILES_PER_TASK,
    )


@app.get("/v1/capabilities", response_model=CapabilitiesResponse)
async def capabilities() -> CapabilitiesResponse:
    return CapabilitiesResponse()



@app.post("/v1/tasks", status_code=202, response_model=TaskCreatedResponse)
async def create_task(
    request: Request,
    files: list[UploadFile] = File(...),
    options: str = Form("{}"),
) -> TaskCreatedResponse | JSONResponse:
    trace_id = request.state.trace_id

    try:
        opts = ParseOptions.model_validate(json.loads(options))
    except Exception as exc:
        return _error(400, ErrorCode.INVALID_OPTIONS, f"options 非法: {exc}",
                      trace_id=trace_id)

    if not files:
        return _error(400, ErrorCode.INVALID_OPTIONS, "files 不能为空", trace_id=trace_id)
    if len(files) > config.MAX_FILES_PER_TASK:
        return _error(400, ErrorCode.TOO_MANY_FILES,
                      f"单任务最多 {config.MAX_FILES_PER_TASK} 个文件",
                      {"got": len(files)}, trace_id)

    if opts.page_range is not None:
        pr = opts.page_range
        if len(pr) != 2 or pr[0] < 1 or pr[1] < pr[0]:
            return _error(400, ErrorCode.INVALID_OPTIONS,
                          "page_range 须为 [start, end]，1-based 且 end>=start",
                          trace_id=trace_id)

    payloads: list[tuple[str, bytes]] = []
    for f in files:
        filename = f.filename or "unnamed"
        ext = Path(filename).suffix.lower()
        if ext not in config.SUPPORTED_EXTENSIONS:
            return _error(415, ErrorCode.UNSUPPORTED_FORMAT,
                          f"不支持的格式: {ext or '(无扩展名)'}",
                          {"filename": filename,
                           "supported": sorted(config.SUPPORTED_EXTENSIONS)},
                          trace_id)
        data = await f.read()
        if len(data) > config.MAX_FILE_SIZE:
            return _error(413, ErrorCode.FILE_TOO_LARGE,
                          f"文件超过 {config.MAX_FILE_SIZE} 字节上限",
                          {"filename": filename, "size": len(data)}, trace_id)
        payloads.append((filename, data))

    try:
        task = task_manager.submit(payloads, opts)
    except QueueFullError as exc:
        return _error(429, ErrorCode.QUEUE_FULL, str(exc), trace_id=trace_id)

    return TaskCreatedResponse(
        task_id=task.task_id,
        total_files=task.response.total_files,
        status_url=f"/v1/tasks/{task.task_id}",
        events_url=f"/v1/tasks/{task.task_id}/events",
    )


@app.get("/v1/tasks/{task_id}", response_model=TaskResponse)
async def get_task(request: Request, task_id: str) -> TaskResponse | JSONResponse:
    try:
        return task_manager.get(task_id).response
    except TaskNotFoundError:
        return _error(404, ErrorCode.TASK_NOT_FOUND, f"任务不存在: {task_id}",
                      trace_id=request.state.trace_id)
    except TaskGoneError:
        return _error(410, ErrorCode.TASK_GONE,
                      f"任务结果已过期（TTL {config.TASK_TTL}s）: {task_id}",
                      trace_id=request.state.trace_id)


@app.get("/v1/tasks/{task_id}/events")
async def task_events(request: Request, task_id: str):
    try:
        task_manager.get(task_id)
    except TaskNotFoundError:
        return _error(404, ErrorCode.TASK_NOT_FOUND, f"任务不存在: {task_id}",
                      trace_id=request.state.trace_id)
    except TaskGoneError:
        return _error(410, ErrorCode.TASK_GONE,
                      f"任务结果已过期（TTL {config.TASK_TTL}s）: {task_id}",
                      trace_id=request.state.trace_id)

    return StreamingResponse(
        sse.event_stream(task_id, task_manager),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/v1/tasks/{task_id}", response_model=TaskResponse)
async def cancel_task(request: Request, task_id: str) -> TaskResponse | JSONResponse:
    try:
        task = task_manager.cancel(task_id)
    except TaskNotFoundError:
        return _error(404, ErrorCode.TASK_NOT_FOUND, f"任务不存在: {task_id}",
                      trace_id=request.state.trace_id)
    except TaskGoneError:
        return _error(410, ErrorCode.TASK_GONE,
                      f"任务结果已过期（TTL {config.TASK_TTL}s）: {task_id}",
                      trace_id=request.state.trace_id)
    except NotCancellableError:
        return _error(409, ErrorCode.TASK_NOT_CANCELLABLE,
                      f"任务已终结，不可取消: {task_id}",
                      trace_id=request.state.trace_id)
    return task.response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        workers=1,  
    )
