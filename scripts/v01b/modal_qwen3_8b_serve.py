"""Serve Qwen/Qwen3-8B (bf16) on one Modal A10G for the v0.1.B hierarchical bullet merge (OpenAI-compatible vLLM).

Deploy: modal deploy scripts/v01b/modal_qwen3_8b_serve.py   Stop: modal app stop qwen3-8b-merge
Auth: Bearer key from Modal secret 'vllm-key' (local copy [REDACTED_PATH], MODAL_VLLM_KEY).
"""
import os
import subprocess
import modal

MODEL = 'Qwen/Qwen3-8B'

# Official vLLM image ships nvcc for vLLM's JIT kernels; python symlink lets Modal run its runtime.
image = (modal.Image.from_registry('vllm/vllm-openai:v0.31.0').entrypoint([])
         .run_commands('ln -sf "$(command -v python3)" /usr/local/bin/python', 'python -m pip install hf_transfer')
         .env({'HF_HUB_ENABLE_HF_TRANSFER': '1'}))
hf_cache = modal.Volume.from_name('hf-cache-qwen3-8b', create_if_missing=True)
app = modal.App('qwen3-8b-merge')


@app.function(image=image, gpu='A10G', volumes={'/root/.cache/huggingface': hf_cache},
              secrets=[modal.Secret.from_name('vllm-key')],
              timeout=3 * 3600, scaledown_window=10 * 60, max_containers=1)
@modal.concurrent(max_inputs=64)
@modal.web_server(port=8000, startup_timeout=30 * 60)
def serve():
    os.environ['VLLM_API_KEY'] = os.environ['MODAL_VLLM_KEY']
    subprocess.Popen(['vllm', 'serve', MODEL, '--dtype', 'bfloat16', '--max-model-len', '16384',
                      '--gpu-memory-utilization', '0.92', '--host', '0.0.0.0', '--port', '8000'])
