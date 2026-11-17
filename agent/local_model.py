"""Lazy, session-local inference for Kaggle GPU runs.

This module has no import-time GPU work. Groq remains the default provider.
"""
from __future__ import annotations

import os
from functools import lru_cache

# Transformers does not need TensorFlow for this PyTorch-only provider. Prevent
# TensorFlow's CUDA probe from printing a misleading no-driver warning on Kaggle.
os.environ.setdefault("USE_TF", "0")


class LocalModel:
    def __init__(self, model_name: str):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        if not torch.cuda.is_available():
            raise RuntimeError("AGENT_PROVIDER=local requires a CUDA GPU")
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            device_map="auto",
            dtype=torch.float16,
            quantization_config=quantization,
        )
        self.model.eval()

    def generate(self, messages: list[dict], *, max_new_tokens: int) -> tuple[str, int, int]:
        prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        encoded = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        input_tokens = int(encoded["input_ids"].shape[-1])
        with self.torch.inference_mode():
            output = self.model.generate(
                **encoded,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=0.7,
                top_p=0.8,
                top_k=20,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        new_tokens = output[0][input_tokens:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip(), input_tokens, len(new_tokens)


@lru_cache(maxsize=1)
def get_local_model(model_name: str) -> LocalModel:
    return LocalModel(model_name)
