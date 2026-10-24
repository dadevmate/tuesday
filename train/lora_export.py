"""MLX LoRA adapter -> GGUF LoRA for llama.cpp's server (tuesday/core.py), without fusing or re-quantizing the base.

MLX computes y = xW + scale * (x @ a) @ b with a: (in, r), b: (r, out). PEFT (what llama.cpp's converter reads) computes
y = xW + (alpha / r) * B(A x) with A: (r, in), B: (out, r). So A = a.T, B = b.T and alpha = scale * r.

  python train/lora_export.py <mlx adapter dir> <base hf repo> <out.gguf>
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from safetensors.numpy import load_file, save_file

OUT = Path(__file__).resolve().parent / "out"
LLAMA_CPP = OUT / "llama.cpp"
CONV_PY = OUT / "convvenv" / "bin" / "python"


def base_config_dir(base_hf: str) -> Path:
    """The base model's config.json from the local Hugging Face cache (the weights aren't needed)."""
    from huggingface_hub.constants import HF_HUB_CACHE
    snaps = sorted((Path(HF_HUB_CACHE) / f"models--{base_hf.replace('/', '--')}" / "snapshots").glob("*"), key=lambda d: d.stat().st_mtime)
    if not snaps:
        from huggingface_hub import snapshot_download
        return Path(snapshot_download(base_hf, allow_patterns=["config.json", "tokenizer*"]))
    return snaps[-1]


def export(adapter_dir: Path, base_hf: str, out: Path, weights: str = "adapters.safetensors") -> Path:
    cfg = json.loads((adapter_dir / "adapter_config.json").read_text())
    lp = cfg.get("lora_parameters", {})
    rank, scale = int(lp.get("rank", 8)), float(lp.get("scale", 20.0))
    tensors = load_file(str(adapter_dir / weights))
    peft, modules = {}, set()
    for k, v in tensors.items():
        stem, kind = k.rsplit(".", 1)          # model.layers.N.self_attn.q_proj , lora_a|lora_b
        modules.add(stem.rsplit(".", 1)[-1])
        name = f"base_model.model.{stem}.lora_{'A' if kind == 'lora_a' else 'B'}.weight"
        peft[name] = np.ascontiguousarray(v.T.astype(np.float32))
    tmp = Path(tempfile.mkdtemp(prefix="peft-"))
    try:
        save_file(peft, str(tmp / "adapter_model.safetensors"))
        (tmp / "adapter_config.json").write_text(json.dumps({
            "peft_type": "LORA", "r": rank, "lora_alpha": scale * rank, "target_modules": sorted(modules),
            "base_model_name_or_path": base_hf, "bias": "none", "task_type": "CAUSAL_LM"}))
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(CONV_PY), str(LLAMA_CPP / "convert_lora_to_gguf.py"), "--base", str(base_config_dir(base_hf)),
                        "--outtype", "f16", "--outfile", str(out), str(tmp)], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"LoRA conversion failed: {e.stderr[-400:]}") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


if __name__ == "__main__":
    print(export(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), *(sys.argv[4:5])))
