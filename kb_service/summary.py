
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


DEFAULT_SUMMARY_PROMPT = """Summarize the following content. Extract only the key information and main points. Remove redundant details.

Requirements:
1. Write a concise summary in plain text
2. You must write in Chinese
3. Focus on important facts, concepts, and details
4. If images are included, describe their key information
5. Do not use words like "好的", "ok", "I understand", "This text discusses", "The content mentions"
6. Write directly without extra words
7. If there is not enough content to generate a meaningful summary, return an empty string without any explanation

Output only the summary text:

{chunk_text}"""



_DEFAULT_LOCAL_MODEL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "dify-main", "docker", "dir",
)
LOCAL_MODEL_PATH = os.getenv("SUMMARY_LOCAL_MODEL_PATH", _DEFAULT_LOCAL_MODEL)


def _local_model_available() -> bool:
    import os as _os
    if not _os.path.isdir(LOCAL_MODEL_PATH):
        return False
    config = _os.path.join(LOCAL_MODEL_PATH, "config.json")
    if not _os.path.isfile(config):
        return False
    try:
        with open(config, "r", encoding="utf-8") as f:
            j = json.load(f)
        if "Qwen2ForCausalLM" not in j.get("architectures", []):
            return False
        required = j.get("transformers_version", "4.43.0")
        from packaging.version import Version
        import transformers
        if Version(transformers.__version__) < Version(required):
            logger.warning(
                f"transformers {transformers.__version__} < 要求 {required}，"
                f"本地模型可能跑不起来"
            )
    except Exception as exc:
        logger.warning(f"本地模型 config.json 解析失败：{exc}")
        return False
    import glob
    safetensors = glob.glob(_os.path.join(LOCAL_MODEL_PATH, "*.safetensors"))
    if not safetensors:
        bin_files = glob.glob(_os.path.join(LOCAL_MODEL_PATH, "*.bin"))
        if not bin_files:
            return False
    try:
        import bitsandbytes  # noqa
    except ImportError:
        logger.warning("bitsandbytes 未安装，INT8 量化不可用。本地模型可能因显存不足 OOM。")
    return True


@dataclass
class _LocalModel:
    model: object = None
    tokenizer: object = None
    loaded: bool = False
    failed: bool = False
    load_error: str = ""


_local_state = _LocalModel()


def _get_local_model() -> _LocalModel:
    if _local_state.loaded or _local_state.failed:
        return _local_state

    if not _local_model_available():
        _local_state.failed = True
        _local_state.load_error = "本地模型不可用（目录不完整）"
        return _local_state

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        quant_config = None
        try:
            import bitsandbytes  # noqa: F401  — 真正需要这个包
            from transformers import BitsAndBytesConfig
            quant_config = BitsAndBytesConfig(
                load_in_8bit=True,
                llm_int8_enable_fp32_cpu_offload=True,
            )
            logger.info("使用 INT8 量化加载（允许 FP32 CPU offload）...")
        except ImportError:
            logger.warning(
                "bitsandbytes 不可用（Windows 上常见），"
                "不量化直接 device_map=auto（约 1.8GB GPU + CPU offload，可能较慢）"
            )

        logger.info(f"正在加载本地模型 {LOCAL_MODEL_PATH}...")
        _local_state.tokenizer = AutoTokenizer.from_pretrained(
            LOCAL_MODEL_PATH,
            trust_remote_code=True,
        )

        if quant_config is not None:
            _local_state.model = AutoModelForCausalLM.from_pretrained(
                LOCAL_MODEL_PATH,
                quantization_config=quant_config,
                device_map="auto",
                trust_remote_code=True,
                low_cpu_mem_usage=True,
            )
        else:
            _local_state.model = AutoModelForCausalLM.from_pretrained(
                LOCAL_MODEL_PATH,
                device_map="auto",
                torch_dtype=torch.bfloat16,
                trust_remote_code=True,
                low_cpu_mem_usage=True,
            )

        _local_state.model.eval()
        _local_state.loaded = True
        logger.info("本地模型加载完成 ✓")
    except Exception as exc:
        _local_state.failed = True
        _local_state.load_error = str(exc)
        logger.warning(f"本地模型加载失败：{exc}")

    return _local_state


def _local_generate(prompt: str, max_new_tokens: int = 128) -> str | None:
    state = _get_local_model()
    if not state.loaded:
        return None

    try:
        import torch

        messages = [{"role": "user", "content": prompt}]
        text = state.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        inputs = state.tokenizer(text, return_tensors="pt")
        input_ids = inputs["input_ids"].to(state.model.device)

        with torch.no_grad():
            outputs = state.model.generate(
                input_ids,
                max_new_tokens=max_new_tokens,
                temperature=0.3,
                do_sample=False,          
                eos_token_id=state.tokenizer.eos_token_id,
                pad_token_id=state.tokenizer.pad_token_id,
            )

        new_tokens = outputs[0][input_ids.shape[-1]:]
        result = state.tokenizer.decode(new_tokens, skip_special_tokens=True)
        return result.strip()
    except Exception as exc:
        logger.warning(f"本地模型生成失败：{exc}")
        return None



_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("SUMMARY_OLLAMA_MODEL", "qwen2.5:3b")
_OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "30"))


def _ollama_available() -> bool:
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(f"{_OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ollama_generate(prompt: str, timeout: int = _OLLAMA_TIMEOUT) -> str | None:
    import urllib.request
    import urllib.error
    try:
        payload = json.dumps({
            "model": _OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 256},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{_OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "").strip()
    except Exception as exc:
        logger.warning(f"Ollama 生成失败：{exc}")
        return None



_CLOUD_API_URL = os.getenv("SUMMARY_CLOUD_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
_CLOUD_API_KEY = os.getenv("GLM_API_KEY", "")
_CLOUD_MODEL = os.getenv("SUMMARY_CLOUD_MODEL", "glm-5.3-flash")


def _cloud_generate(prompt: str) -> str | None:
    import urllib.request
    import urllib.error
    if not _CLOUD_API_KEY:
        return None
    try:
        payload = json.dumps({
            "model": _CLOUD_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }).encode("utf-8")
        req = urllib.request.Request(
            _CLOUD_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_CLOUD_API_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return content.strip()
    except Exception as exc:
        logger.warning(f"云端摘要生成失败：{exc}")
        return None



def _split_model_ref(ref: str) -> tuple[int, str] | None:
    try:
        provider_id, model = (ref or "").split(":", 1)
        provider_id, model = int(provider_id), model.strip()
    except (ValueError, AttributeError):
        return None
    if provider_id <= 0 or not model:
        return None
    return provider_id, model


def _configured_generate(prompt: str, tenant_id: int, provider_id: int, model: str) -> str | None:
    import urllib.error
    import urllib.request
    try:
        from sqlalchemy import select

        from allfusion_auth.crypto import decrypt_secret
        from allfusion_auth.db import get_engine
        from allfusion_auth.models import model_provider

        with get_engine().connect() as conn:
            row = conn.execute(select(
                model_provider.c.base_url,
                model_provider.c.completions_path,
                model_provider.c.api_key_cipher,
                model_provider.c.key_version,
            ).where(
                model_provider.c.id == provider_id,
                model_provider.c.tenant_id == tenant_id,
                model_provider.c.enabled == 1,
            )).first()
        if row is None or not row.api_key_cipher:
            return None
        api_key = decrypt_secret(bytes(row.api_key_cipher), int(row.key_version or 1))
        if not api_key:
            return None
        base = (row.base_url or "").strip().rstrip("/")
        path = (row.completions_path or "/chat/completions").strip()
        if not path.startswith("/"):
            path = "/" + path
        api_url = base + path
    except Exception as exc:
        logger.warning(f"摘要模型配置读取失败：{exc}")
        return None

    try:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = (data["choices"][0]["message"].get("content") or "").strip()
        return content or None
    except Exception as exc:
        logger.warning(f"摘要模型调用失败（{model}）：{exc}")
        return None


@dataclass
class SummaryResult:
    text: str
    model_used: str
    latency_ms: int
    success: bool


class SummaryGenerator:

    def __init__(
        self,
        prompt: str = DEFAULT_SUMMARY_PROMPT,
        prefer_cloud: bool = False,
        summary_model: str = "",
        tenant_id: int = 0,
    ):
        self.prompt = prompt
        self.prefer_cloud = prefer_cloud
        self.summary_model = summary_model
        self.tenant_id = tenant_id


    def generate(self, chunk_text: str, min_chars: int = 20) -> SummaryResult:
        text = chunk_text.strip()
        if len(text) < min_chars:
            return SummaryResult(text="", model_used="none", latency_ms=0, success=True)

        prompt = self.prompt.format(chunk_text=text)
        start = time.perf_counter()

        if self.summary_model and self.tenant_id > 0:
            ref = _split_model_ref(self.summary_model)
            if ref is not None:
                text = _configured_generate(prompt, self.tenant_id, ref[0], ref[1])
                latency_ms = int((time.perf_counter() - start) * 1000)
                if text is None:
                    return SummaryResult(text="", model_used=f"configured_failed({ref[1]})",
                                         latency_ms=latency_ms, success=False)
                return SummaryResult(text=text.strip(), model_used=f"configured({ref[1]})",
                                     latency_ms=latency_ms, success=True)

        result_text = None
        model_tag = "none"

        if result_text is None and self.prefer_cloud:
            result_text = _cloud_generate(prompt)
            if result_text is not None:
                model_tag = f"cloud({_CLOUD_MODEL})"

        if result_text is None:
            result_text = _local_generate(prompt)
            if result_text is not None:
                model_tag = f"local_transformers({os.path.basename(LOCAL_MODEL_PATH)})"

        if result_text is None and _ollama_available():
            result_text = _ollama_generate(prompt)
            if result_text is not None:
                model_tag = f"ollama({_OLLAMA_MODEL})"

        if result_text is None:
            result_text = _cloud_generate(prompt)
            if result_text is not None:
                model_tag = f"cloud({_CLOUD_MODEL})"

        latency_ms = int((time.perf_counter() - start) * 1000)

        if result_text is None:
            return SummaryResult(text="", model_used=model_tag, latency_ms=latency_ms, success=False)

        return SummaryResult(
            text=result_text.strip(),
            model_used=model_tag,
            latency_ms=latency_ms,
            success=True,
        )


    def generate_batch(
        self,
        chunk_texts: list[str],
        progress_cb=None,
    ) -> list[SummaryResult]:
        results: list[SummaryResult] = []
        for i, text in enumerate(chunk_texts):
            result = self.generate(text)
            results.append(result)
            if progress_cb:
                progress_cb(i + 1, len(chunk_texts), result)
        return results



def _test_prompt_format() -> None:
    gen = SummaryGenerator()
    sample = "这是一段测试文本，介绍了 AllFusion 项目的架构。"
    prompt = gen.prompt.format(chunk_text=sample)
    assert sample in prompt, "chunk_text 应被注入 prompt"
    assert "Summarize" in prompt
    print("✓ prompt 格式化正确")


def _test_local_model_detection() -> None:
    ok = _local_model_available()
    print(f"本地模型目录: {LOCAL_MODEL_PATH}")
    print(f"本地模型可用: {ok}")
    if ok:
        state = _get_local_model()
        print(f"模型已加载: {state.loaded}")
    else:
        print(f"（跳过本地加载测试）")


if __name__ == "__main__":
    _test_prompt_format()
    _test_local_model_detection()

