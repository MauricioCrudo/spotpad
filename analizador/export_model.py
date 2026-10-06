"""
Prepara el modelo para el analizador (se corre al compilar, en GitHub Actions; no en tu máquina).

  1. Baja SigLIP (google/siglip-base-patch16-224, licencia Apache 2.0) de Hugging Face.
  2. Exporta la parte visual a ONNX (modelo/vision.onnx). Con --int8 la comprime (pesa ~4 veces
     menos pero corre solo en CPU); por defecto queda en fp32 para usar la GPU (DirectML / CoreML).
  3. Calcula los embeddings de las descripciones de surfaces.json (modelo/text_emb.npz).

Uso:  python export_model.py [--out modelo] [--model google/siglip-base-patch16-224]
Necesita: torch, transformers, sentencepiece, protobuf, onnx, onnxruntime.
"""
import argparse
import os
import shutil
import tempfile

import numpy as np

from surfaces import load_config, prompt_groups, prompts_hash


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "modelo"))
    ap.add_argument("--model", default="google/siglip-base-patch16-224")
    ap.add_argument("--int8", action="store_true", help="comprimir a int8 (4 veces más chico, solo CPU)")
    a = ap.parse_args()

    import torch
    import torch.nn.functional as F
    from transformers import AutoTokenizer, SiglipModel

    os.makedirs(a.out, exist_ok=True)
    model = SiglipModel.from_pretrained(a.model).eval()
    tok = AutoTokenizer.from_pretrained(a.model)

    class VisionOnly(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, pixel_values):
            return F.normalize(self.m.get_image_features(pixel_values=pixel_values), dim=-1)

    vis = VisionOnly(model).eval()
    x = torch.randn(2, 3, 224, 224)
    tmp = tempfile.mkdtemp()
    fp32 = os.path.join(tmp, "vision_fp32.onnx")
    kw = dict(input_names=["pixel_values"], output_names=["image_embeds"],
              dynamic_axes={"pixel_values": {0: "batch"}, "image_embeds": {0: "batch"}}, opset_version=17)
    try:
        torch.onnx.export(vis, (x,), fp32, dynamo=False, **kw)
    except TypeError:                        # torch viejo sin «dynamo»
        torch.onnx.export(vis, (x,), fp32, **kw)

    import onnxruntime as ort
    with torch.no_grad():
        ref = vis(x).numpy()
    run = lambda p: ort.InferenceSession(p, providers=["CPUExecutionProvider"]).run(None, {"pixel_values": x.numpy()})[0]
    c32 = float((run(fp32) * ref).sum(axis=1).min())
    print(f"ONNX fp32 vs torch: coseno mínimo {c32:.5f}")
    assert c32 > 0.999, "La exportación a ONNX no coincide con el modelo original"

    # Imágenes de prueba con estructura (gradientes y texturas), no solo ruido
    g = torch.linspace(-1, 1, 224)
    imgs = torch.stack([torch.stack([g[None, :].expand(224, 224), g[:, None].expand(224, 224),
                                     torch.sin(g[None, :] * k * 3.14).expand(224, 224)]) for k in range(1, 9)])
    final = fp32
    if a.int8:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        q8 = os.path.join(tmp, "vision_int8.onnx")
        quantize_dynamic(fp32, q8, weight_type=QuantType.QInt8)
        with torch.no_grad():
            r2 = vis(imgs).numpy()
        sess = ort.InferenceSession(q8, providers=["CPUExecutionProvider"])
        c8 = float((sess.run(None, {"pixel_values": imgs.numpy()})[0] * r2).sum(axis=1).min())
        print(f"ONNX int8 vs torch: coseno mínimo {c8:.4f}")
        if c8 >= 0.97:
            final = q8
        else:
            print("int8 se aleja demasiado: uso fp32")
    shutil.copy(final, os.path.join(a.out, "vision.onnx"))
    print("vision.onnx:", round(os.path.getsize(os.path.join(a.out, "vision.onnx")) / 1e6), "MB")

    cfg = load_config()
    out = {}
    with torch.no_grad():
        for name, prompts in prompt_groups(cfg).items():
            t = tok(prompts, padding="max_length", max_length=64, truncation=True, return_tensors="pt")
            out[name] = F.normalize(model.get_text_features(input_ids=t["input_ids"]), dim=-1).numpy().astype(np.float32)
        out["logit_scale"] = np.array(float(model.logit_scale.exp()), np.float32)
        out["logit_bias"] = np.array(float(model.logit_bias), np.float32)
    out["hash"] = np.array(prompts_hash(cfg))
    np.savez(os.path.join(a.out, "text_emb.npz"), **out)
    print("text_emb.npz:", {k: v.shape for k, v in out.items()})

    # Chequeo rápido: texturas sintéticas → qué superficie elige (solo informativo)
    from surfaces import frame_scores
    with torch.no_grad():
        e = vis(imgs[:4]).numpy()
    w, probs, _ = frame_scores(e, e, out, cfg, float(out["logit_scale"]))
    print("prueba (piso visible, superficie):",
          [(round(float(a), 2), cfg["surfaces"][int(p.argmax())]["key"]) for a, p in zip(w, probs)])
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
