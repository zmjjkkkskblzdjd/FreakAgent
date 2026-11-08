from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field



class ErrorCode(str, Enum):
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    TOO_MANY_FILES = "TOO_MANY_FILES"
    QUEUE_FULL = "QUEUE_FULL"
    PARSE_TIMEOUT = "PARSE_TIMEOUT"
    PARSE_FAILED = "PARSE_FAILED"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_GONE = "TASK_GONE"
    TASK_NOT_CANCELLABLE = "TASK_NOT_CANCELLABLE"
    INVALID_OPTIONS = "INVALID_OPTIONS"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class ErrorResponse(BaseModel):
    error: ErrorBody



class ParseOptions(BaseModel):
    output_format: Literal["markdown"] = "markdown"
    do_ocr: bool = True
    do_table_structure: bool = True
    do_formula: bool = False
    extract_images: bool = False
    page_range: Optional[list[int]] = None  
    callback_url: Optional[str] = None
    priority: Literal["low", "normal", "high"] = "normal"



class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL_SUCCESS = "partial_success"
    CANCELLED = "cancelled"


class FileState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  


class FileError(BaseModel):
    code: ErrorCode
    message: str


class FileResult(BaseModel):
    file_id: str
    filename: str
    status: FileState = FileState.PENDING
    markdown: Optional[str] = None
    page_count: Optional[int] = None
    duration_ms: Optional[int] = None
    error: Optional[FileError] = None


class TaskResponse(BaseModel):
    task_id: str
    status: TaskState
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total_files: int
    completed_files: int = 0
    failed_files: int = 0
    results: list[FileResult] = Field(default_factory=list)
    expires_at: Optional[float] = None


class TaskCreatedResponse(BaseModel):
    task_id: str
    status: TaskState = TaskState.PENDING
    total_files: int
    status_url: str
    events_url: str



class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    model_loaded: bool
    queue_size: int
    running_tasks: int
    uptime_s: float
    backend: str = "doc_parser"


class FormatsResponse(BaseModel):
    formats: list[str]
    max_file_size: int
    max_files_per_task: int


class CapabilitiesResponse(BaseModel):
    ocr: bool = True
    table_structure: bool = True
    formula: bool = True
    image_extraction: bool = True
    page_range: bool = True
    batch: bool = True
    sse_progress: bool = True
    cancellation: bool = True


def now() -> float:
    return time.time()
