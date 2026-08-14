#!/usr/bin/env python3
"""
OpenAI Compatible Image Generation Backend

Generates images via OpenAI-compatible APIs (OpenAI, local models like Qwen-Image, etc.).
Used by image_gen.py as a backend module.

Configuration keys:
  OPENAI_API_KEY   (required) API key
  OPENAI_BASE_URL  (optional) Custom API endpoint (e.g. http://127.0.0.1:3000/v1)
  OPENAI_MODEL     (optional) Model name (default: gpt-image-1)

Dependencies:
  pip install openai Pillow
"""

import base64
import os
import time
import threading
from urllib.parse import urljoin

from openai import OpenAI
import requests
from image_backends.backend_common import (
    MAX_RETRIES,
    download_image,
    http_error,
    is_rate_limit_error,
    normalize_image_size,
    resolve_output_path,
    retry_delay,
    save_image_bytes,
)


# ╔══════════════════════════════════════════════════════════════════╗
# ║  Constants                                                      ║
# ╚══════════════════════════════════════════════════════════════════╝

# Aspect ratios supported by the CLI. OpenAI-compatible image gateways usually
# accept a small set of concrete sizes, so ratios are bucketed by orientation.
VALID_ASPECT_RATIOS = ["1:1", "16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "21:9"]

OPENAI_SIZE_BY_TIER_ORIENTATION = {
    # 512px is kept as a low-quality alias for callers that still pass it.
    # gpt-image-2 requires at least about 0.7 MP, so use the 1K canvas.
    "512px": {
        "square": "1024x1024",
        "landscape": "1536x1024",
        "portrait": "1024x1536",
    },
    "1K": {
        "square": "1024x1024",
        "landscape": "1536x1024",
        "portrait": "1024x1536",
    },
    "2K": {
        "square": "2048x2048",
        "landscape": "2560x1440",
        "portrait": "1440x2560",
    },
    "4K": {
        "square": "2880x2880",
        "landscape": "3840x2160",
        "portrait": "2160x3840",
    },
}

# image_size -> quality mapping
IMAGE_SIZE_TO_QUALITY = {
    "512px": "low",
    "1K":    "auto",
    "2K":    "high",
    "4K":    "high",
}

DEFAULT_MODEL = "gpt-image-1"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _orientation_for_ratio(aspect_ratio: str) -> str:
    if aspect_ratio == "1:1":
        return "square"
    try:
        width, height = [int(part) for part in aspect_ratio.split(":", 1)]
    except Exception:
        return "square"
    return "landscape" if width > height else "portrait"


def _resolve_openai_size(aspect_ratio: str, image_size: str) -> str:
    tier = image_size if image_size in OPENAI_SIZE_BY_TIER_ORIENTATION else "1K"
    orientation = _orientation_for_ratio(aspect_ratio)
    return OPENAI_SIZE_BY_TIER_ORIENTATION[tier][orientation]


def _unwrap_payload(payload: dict) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        return data
    return payload


def _extract_image_ref(payload: dict) -> tuple[str | None, str | None]:
    """Return (base64, url) from common OpenAI and async gateway response shapes."""
    if not isinstance(payload, dict):
        return None, None

    data = payload.get("data")
    if isinstance(data, list) and data:
        item = data[0] if isinstance(data[0], dict) else {}
        return item.get("b64_json"), item.get("url")

    result_b64 = payload.get("result_b64") or payload.get("result_b64s")
    if isinstance(result_b64, list) and result_b64:
        return str(result_b64[0]), None
    if isinstance(result_b64, str):
        return result_b64, None

    result_urls = payload.get("result_urls") or payload.get("urls")
    if isinstance(result_urls, list) and result_urls:
        return None, str(result_urls[0])
    if isinstance(result_urls, str):
        return None, result_urls

    return payload.get("b64_json"), payload.get("url")


def _decode_b64_image(value: str) -> bytes:
    if "," in value and value.strip().lower().startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value)


def _should_use_async_gateway(base_url: str | None) -> bool:
    override = os.environ.get("OPENAI_IMAGE_ASYNC_SYNC") or os.environ.get("OPENAI_IMAGE_ASYNC")
    if override:
        return _truthy(override)
    return "65535.space" in (base_url or "").lower()


def _save_image_ref(
    *,
    prompt: str,
    output_dir: str | None,
    filename: str | None,
    b64_value: str | None,
    url_value: str | None,
    headers: dict[str, str] | None = None,
) -> str:
    path = resolve_output_path(prompt, output_dir, filename, ".png")
    if b64_value:
        return save_image_bytes(_decode_b64_image(b64_value), path)
    if url_value:
        return download_image(url_value, path, headers=headers, timeout=180)
    raise RuntimeError("No image data was returned.")


def _generate_image_via_async_gateway(
    *,
    api_key: str,
    prompt: str,
    size: str,
    output_dir: str | None,
    filename: str | None,
    model: str,
    base_url: str,
) -> str:
    base = base_url.rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "official_fallback": False,
    }
    timeout_seconds = int(os.environ.get("OPENAI_IMAGE_TIMEOUT_SEC") or os.environ.get("HERMES_IMAGE_TIMEOUT_SEC") or "600")
    poll_interval = float(os.environ.get("OPENAI_IMAGE_POLL_INTERVAL_SEC") or "2")

    response = requests.post(
        f"{base}/images/generations",
        headers=headers,
        json=body,
        timeout=180,
    )
    if response.status_code >= 400:
        raise http_error(response, "OpenAI-compatible image submit")

    submit_payload = _unwrap_payload(response.json())
    b64_value, url_value = _extract_image_ref(submit_payload)
    if b64_value or url_value:
        return _save_image_ref(
            prompt=prompt,
            output_dir=output_dir,
            filename=filename,
            b64_value=b64_value,
            url_value=url_value,
            headers=headers,
        )

    job_id = submit_payload.get("job_id") or submit_payload.get("task_id") or submit_payload.get("id")
    if not job_id:
        raise RuntimeError(f"Image gateway did not return job_id or image data: {submit_payload}")

    raw_status_url = submit_payload.get("status_url")
    poll_url = urljoin(base + "/", raw_status_url) if raw_status_url else f"{base}/images/async-generations/{job_id}"
    print(f"  Job ID:       {job_id}")

    start = time.time()
    last_status = "submitted"
    while True:
        elapsed = time.time() - start
        if elapsed > timeout_seconds:
            raise RuntimeError(f"Timed out after {timeout_seconds}s while polling image job {job_id}")

        time.sleep(poll_interval)
        poll_resp = requests.get(poll_url, headers={"Authorization": headers["Authorization"]}, timeout=180)
        if poll_resp.status_code == 404:
            print(f"  Status:       {last_status} ({elapsed:.0f}s, waiting for job)")
            continue
        if poll_resp.status_code >= 400:
            raise http_error(poll_resp, "OpenAI-compatible image poll")

        poll_payload = _unwrap_payload(poll_resp.json())
        status = str(poll_payload.get("status") or poll_payload.get("state") or last_status).strip().lower()
        last_status = status or last_status
        print(f"  Status:       {last_status} ({elapsed:.0f}s)")

        if status in {"done", "ready", "success", "succeeded", "completed"}:
            b64_value, url_value = _extract_image_ref(poll_payload)
            return _save_image_ref(
                prompt=prompt,
                output_dir=output_dir,
                filename=filename,
                b64_value=b64_value,
                url_value=url_value,
                headers=headers,
            )

        if status in {"error", "failed", "fail", "cancelled", "canceled"}:
            raise RuntimeError(f"Remote image generation failed: {poll_payload}")


# ╔══════════════════════════════════════════════════════════════════╗
# ║  Image Generation                                               ║
# ╚══════════════════════════════════════════════════════════════════╝

def _generate_image(api_key: str, prompt: str, negative_prompt: str = None,
                    aspect_ratio: str = "1:1", image_size: str = "1K",
                    output_dir: str = None, filename: str = None,
                    model: str = DEFAULT_MODEL, base_url: str = None) -> str:
    """
    Image generation via OpenAI-compatible API.

    Maps aspect_ratio and image_size to OpenAI's size parameter.

    Returns:
        Path of the saved image file

    Raises:
        RuntimeError: When generation fails
    """
    # Build prompt (OpenAI has no native negative_prompt, append to prompt)
    final_prompt = prompt
    if negative_prompt:
        final_prompt += f"\n\nAvoid the following: {negative_prompt}"

    # Map parameters
    size = _resolve_openai_size(aspect_ratio, image_size)
    quality = IMAGE_SIZE_TO_QUALITY.get(image_size, "auto")

    mode_label = f"Proxy: {base_url}" if base_url else "OpenAI API"
    print(f"[OpenAI - {mode_label}]")
    print(f"  Model:        {model}")
    print(f"  Prompt:       {final_prompt[:120]}{'...' if len(final_prompt) > 120 else ''}")
    print(f"  Size:         {size} (from aspect_ratio={aspect_ratio}, image_size={image_size})")
    print(f"  Quality:      {quality} (from image_size={image_size})")
    print()

    start_time = time.time()
    print(f"  [..] Generating...", end="", flush=True)

    # Heartbeat thread
    heartbeat_stop = threading.Event()

    def _heartbeat():
        while not heartbeat_stop.is_set():
            heartbeat_stop.wait(5)
            if not heartbeat_stop.is_set():
                elapsed = time.time() - start_time
                print(f" {elapsed:.0f}s...", end="", flush=True)

    hb_thread = threading.Thread(target=_heartbeat, daemon=True)
    hb_thread.start()

    try:
        if _should_use_async_gateway(base_url):
            path = _generate_image_via_async_gateway(
                api_key=api_key,
                prompt=final_prompt,
                size=size,
                output_dir=output_dir,
                filename=filename,
                model=model,
                base_url=base_url,
            )
            resp = None
        else:
            client = OpenAI(api_key=api_key, base_url=base_url)
            resp = client.images.generate(
                prompt=final_prompt,
                model=model,
                size=size,
                quality=quality,
                n=1,
                response_format="b64_json",
            )
    finally:
        heartbeat_stop.set()
        hb_thread.join(timeout=1)

    elapsed = time.time() - start_time
    print(f"\n  [DONE] Image generated ({elapsed:.1f}s)")

    if resp is None:
        return path

    if resp is not None and resp.data:
        path = resolve_output_path(prompt, output_dir, filename, ".png")
        image_data = base64.b64decode(resp.data[0].b64_json)
        return save_image_bytes(image_data, path)

    raise RuntimeError("No image was generated. The server may have refused the request.")


# ╔══════════════════════════════════════════════════════════════════╗
# ║  Public Entry Point                                             ║
# ╚══════════════════════════════════════════════════════════════════╝

def generate(prompt: str, negative_prompt: str = None,
             aspect_ratio: str = "1:1", image_size: str = "1K",
             output_dir: str = None, filename: str = None,
             model: str = None, max_retries: int = MAX_RETRIES) -> str:
    """
    OpenAI-compatible image generation with automatic retry.

    Reads credentials from the current process environment or the project-root `.env`:
      OPENAI_API_KEY
      OPENAI_BASE_URL
      OPENAI_MODEL (optional override)

    Args:
        prompt: Positive prompt text
        negative_prompt: Negative prompt text (appended to prompt as "Avoid...")
        aspect_ratio: Aspect ratio, mapped to OpenAI size
        image_size: Image size, mapped to OpenAI quality
        output_dir: Output directory
        filename: Output filename (without extension)
        model: Model name (default: gpt-image-1)
        max_retries: Maximum number of retries

    Returns:
        Path of the saved image file
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")

    if not api_key:
        raise ValueError(
            "No API key found. Set OPENAI_API_KEY in the current environment or the project-root .env."
        )

    if model is None:
        model = os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL

    image_size = normalize_image_size(image_size)

    if aspect_ratio not in VALID_ASPECT_RATIOS:
        supported = list(VALID_ASPECT_RATIOS)
        raise ValueError(
            f"Unsupported aspect ratio '{aspect_ratio}' for OpenAI backend. "
            f"Supported: {supported}"
        )

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return _generate_image(api_key, prompt, negative_prompt,
                                   aspect_ratio, image_size, output_dir,
                                   filename, model, base_url)
        except Exception as e:
            last_error = e
            if attempt < max_retries and is_rate_limit_error(e):
                delay = retry_delay(attempt, rate_limited=True)
                print(f"\n  [WARN] Rate limit hit (attempt {attempt + 1}/{max_retries + 1}). "
                      f"Waiting {delay}s before retry...")
                time.sleep(delay)
            elif attempt < max_retries:
                delay = retry_delay(attempt, rate_limited=False)
                print(f"\n  [WARN] Error (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                      f"Retrying in {delay}s...")
                time.sleep(delay)
            else:
                break

    raise RuntimeError(f"Failed after {max_retries + 1} attempts. Last error: {last_error}")
