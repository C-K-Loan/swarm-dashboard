"""Serve Qwen3-30B-A3B-Instruct-2507 in bf16 + the v0.3-lean LoRA on one Modal H100 (OpenAI-compatible vLLM).

Deploy: modal deploy scripts/v02/modal_lora_serve.py   (prints the https URL)
Models: base = "Qwen/Qwen3-30B-A3B-Instruct-2507", LoRA = "lora-mt1". Stop: modal app stop qwen30b-lora-eval
"""
import os
import subprocess
import modal

BASE = 'Qwen/Qwen3-30B-A3B-Instruct-2507'
ADAPTER = 'data/sft/checkpoints/qwen30b_r16_mt1'
ADAPTER_V2 = 'data/sft/checkpoints/qwen30b_r16_mt2'  # served as "lora-mt2" once on disk at deploy time

# Official vLLM image: ships the CUDA toolkit (nvcc) that vLLM's JIT kernels need; debian_slim does not.
image = (modal.Image.from_registry('vllm/vllm-openai:v0.31.0').entrypoint([])
         .run_commands('ln -sf "$(command -v python3)" /usr/local/bin/python', 'python -m pip install hf_transfer')
         .env({'HF_HUB_ENABLE_HF_TRANSFER': '1'})
         .add_local_dir(ADAPTER, remote_path='/adapter'))
if os.path.isdir(ADAPTER_V2):image=image.add_local_dir(ADAPTER_V2,remote_path='/adapter-v2')
hf_cache = modal.Volume.from_name('hf-cache-qwen30b', create_if_missing=True)
app = modal.App('qwen30b-lora-eval')


@app.function(image=image, gpu='H100', volumes={'/root/.cache/huggingface': hf_cache},
              secrets=[modal.Secret.from_name('vllm-key')],
              timeout=3 * 3600, scaledown_window=15 * 60, max_containers=1)
@modal.concurrent(max_inputs=256)
@modal.web_server(port=8000, startup_timeout=40 * 60)
def serve():
    import os
    os.environ['VLLM_API_KEY'] = os.environ['MODAL_VLLM_KEY']  # vLLM requires Bearer auth; key in [REDACTED_PATH]
    loras = ['lora-mt1=/adapter'] + (['lora-mt2=/adapter-v2'] if os.path.isdir('/adapter-v2') else [])
    subprocess.Popen(['vllm', 'serve', BASE, '--dtype', 'bfloat16', '--max-model-len', '16384',
                      '--enable-lora', '--max-lora-rank', '16', '--max-loras', '2', '--lora-modules', *loras,
                      '--gpu-memory-utilization', '0.92', '--host', '0.0.0.0', '--port', '8000'])
