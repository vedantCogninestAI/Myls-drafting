import boto3
import structlog
from typing import Callable, TypeVar

from app.config import settings

T = TypeVar("T")
logger = structlog.get_logger(__name__)


def _get_bedrock_client():
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def retry_llm_call(func: Callable[..., T], *args, **kwargs) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, settings.LLM_MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "llm_call_retry",
                func=func.__name__,
                attempt=attempt,
                max_attempts=settings.LLM_MAX_RETRIES,
                error=str(exc),
            )
    logger.error("llm_call_exhausted", func=func.__name__, max_attempts=settings.LLM_MAX_RETRIES)
    raise last_exc


def invoke_with_text(prompt: str, context: str) -> str:
    client = _get_bedrock_client()
    response = client.converse(
        modelId=settings.BEDROCK_MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [{"text": f"{prompt}\n\n{context}"}],
            }
        ],
    )
    return response["output"]["message"]["content"][0]["text"].strip()


def invoke_with_image(image_bytes: bytes, prompt: str) -> str:
    client = _get_bedrock_client()
    response = client.converse(
        modelId=settings.BEDROCK_MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": "png",
                            "source": {"bytes": image_bytes},
                        }
                    },
                    {"text": prompt},
                ],
            }
        ],
    )
    return response["output"]["message"]["content"][0]["text"]


def invoke_with_tools(
    messages: list[dict],
    tools: list[dict],
    system_prompt: str | None = None,
    tool_choice: str = "auto",
) -> tuple[dict, str]:
    """One turn of a tool-calling conversation. Returns (assistant_message, stop_reason).

    Callers own the loop: append the returned assistant_message to `messages`,
    and if stop_reason == "tool_use", execute the requested tool, append a
    "toolResult" user message, and call this again. Kept as a single-call
    primitive (not a loop) so retry_llm_call retries one API call, not an
    entire multi-turn conversation.

    tool_choice: "auto" (model decides whether to call a tool) or "any"
    (model must call one of the given tools every turn — use this to
    structurally prevent free-text commentary when every valid response
    should be a tool call).
    """
    client = _get_bedrock_client()
    kwargs = {
        "modelId": settings.BEDROCK_MODEL_ID,
        "messages": messages,
        "toolConfig": {"tools": tools, "toolChoice": {tool_choice: {}}},
        # Without an explicit limit, Bedrock falls back to a small implicit
        # default that's fine for short outputs (OCR/classification) but
        # cuts off a full draft document before it's finished.
        "inferenceConfig": {"maxTokens": 8192},
    }
    if system_prompt:
        kwargs["system"] = [{"text": system_prompt}]
    response = client.converse(**kwargs)
    return response["output"]["message"], response["stopReason"]
