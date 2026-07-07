import boto3
from typing import Callable, TypeVar

from app.config import settings

T = TypeVar("T")


def _get_bedrock_client():
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def retry_llm_call(func: Callable[..., T], *args, **kwargs) -> T:
    last_exc: Exception | None = None
    for _ in range(settings.LLM_MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
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
