"""
QwenEngine — Online vision-language model engine
=================================================
Replaces the three-model offline stack (OWLv2 + Florence-2 + Llama) with a
single API call to a powerful VLM such as Qwen2-VL.

Uses the OpenAI-compatible chat API format, which is supported by:
  - Alibaba DashScope  (Qwen2-VL-Max, Qwen2-VL-72B)
  - OpenAI             (gpt-4o, gpt-4-turbo)
  - Any OpenAI-compatible endpoint (Ollama, vLLM, etc.)

Debug tips:
    engine = QwenEngine(api_key="...", model_name="qwen-vl-max")
    img    = Image.open("test.png").convert("RGB")
    raw    = engine.query_image(img, "Describe this scene.")
    print(raw)
"""

import base64
import gc
import io
import json
import re

from PIL import Image
from openai import OpenAI


class QwenEngine:
    """
    Thin wrapper around an OpenAI-compatible multimodal chat API.

    The engine encodes the image as a base64 JPEG, attaches it to the
    user message, and returns the raw text response. JSON parsing and
    post-processing are handled by the caller (online.py).
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen-vl-max",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        max_image_side: int = 1024,
        max_tokens: int = 1500,
        extra_body: dict = None,
        temperature: float = 0.0,
    ):
        """
        Args:
            api_key:         API key for the chosen provider.
            model_name:      Model identifier string.
            base_url:        OpenAI-compatible endpoint.
                             Set to None to use the default OpenAI endpoint.
            max_image_side:  Images are resized to this on the longest edge
                             before encoding to reduce token cost.
            max_tokens:      Maximum tokens in the model response.
            extra_body:      Provider-specific request fields (e.g. OpenRouter
                             {"provider": {"sort": "latency"}}).
            temperature:     0 = deterministic answers (same frame → same answer).
        """
        self.model_name      = model_name
        self.max_image_side  = max_image_side
        self.max_tokens      = max_tokens
        self.extra_body      = extra_body
        self.temperature     = temperature
        self.client          = OpenAI(api_key=api_key, base_url=base_url)
        print(f"QwenEngine ready: {model_name} @ {base_url or 'OpenAI default'}")

    # ── Image encoding ────────────────────────────────────────────────────────

    def _encode_image(self, image: Image.Image) -> str:
        """Resize and base64-encode a PIL Image as JPEG for the API."""
        w, h = image.size
        if max(w, h) > self.max_image_side:
            scale = self.max_image_side / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    # ── Core API call ─────────────────────────────────────────────────────────

    def query_image(
        self,
        image: Image.Image,
        system_prompt: str,
        user_text: str = "Analyze this scene for hazards and return the JSON assessment.",
    ) -> str:
        """
        Send image + prompts to the VLM and return the raw text response.

        Args:
            image:         PIL Image of the scene.
            system_prompt: Full system instructions (from prompts.py).
            user_text:     Short task instruction appended alongside the image.

        Returns:
            Raw string response from the model (usually a JSON block).
        """
        b64 = self._encode_image(image)

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                        {"type": "text", "text": user_text},
                    ],
                },
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            extra_body=self.extra_body,
        )
        return response.choices[0].message.content

    # ── JSON parsing ──────────────────────────────────────────────────────────

    @staticmethod
    def parse_json(response: str) -> dict:
        """
        Robustly extract and parse JSON from the VLM response.

        Handles:
          - Markdown code fences (```json ... ```)
          - Leading / trailing prose
          - Truncated JSON (closes open braces/brackets before raising)
        """
        clean = re.sub(r'^```json?\s*|\s*```$', '', response.strip())

        json_match = re.search(r'\{[\s\S]*\}', clean)
        if not json_match:
            raise ValueError("No JSON object found in VLM response")

        candidate = json_match.group()

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            open_braces   = candidate.count('{') - candidate.count('}')
            open_brackets = candidate.count('[') - candidate.count(']')
            repaired = candidate + ']' * max(0, open_brackets) + '}' * max(0, open_braces)
            return json.loads(repaired)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self):
        """Release client resources."""
        del self.client
        gc.collect()
