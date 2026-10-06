"""
Modelo visual: SigLIP (Google, abierto) exportado a ONNX, corre 100 % local con onnxruntime.

  vision.onnx   → imagen 224×224 → embedding normalizado
  text_emb.npz  → embeddings de las descripciones de superficies (calculados al compilar, ver
                  export_model.py), más logit_scale/logit_bias del modelo.

En Mac usa CoreML (GPU/Neural Engine) y en Windows DirectML (cualquier GPU, incluida la 3070) si
están; si no, CPU. Nunca se conecta a internet.
"""
import os
import sys

import numpy as np

SIZE = 224


def model_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("SPOTPAD_MODEL_DIR") or os.path.join(base, "modelo")


def _providers():
    import onnxruntime as ort
    have = ort.get_available_providers()
    pref = [p for p in ("CoreMLExecutionProvider", "DmlExecutionProvider", "CUDAExecutionProvider") if p in have]
    return pref + ["CPUExecutionProvider"]


class Vision:
    def __init__(self, folder=None, log=print):
        import onnxruntime as ort
        folder = folder or model_dir()
        path = os.path.join(folder, "vision.onnx")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Falta el modelo ({path})")
        self.path = path
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.sess = None
        for prov in (_providers(), ["CPUExecutionProvider"]):
            try:
                self.sess = ort.InferenceSession(path, so, providers=prov)
                self.provider = self.sess.get_providers()[0]
                break
            except Exception as ex:          # CoreML/DirectML que no arranca → CPU
                log(f"Modelo: {prov[0]} no disponible ({ex}); uso CPU")
        self.input = self.sess.get_inputs()[0].name
        t = np.load(os.path.join(folder, "text_emb.npz"), allow_pickle=False)
        self.text = {k: t[k] for k in t.files}
        self.scale = float(self.text.get("logit_scale", np.array(100.0)))
        self.bias = float(self.text.get("logit_bias", np.array(0.0)))

    def embed(self, images, batch=16):
        """images: lista de arrays uint8 (224, 224, 3) RGB → (N, D) normalizados."""
        out = []
        for a in range(0, len(images), batch):
            x = np.stack(images[a:a + batch]).astype(np.float32) / 255.0
            x = (x - 0.5) / 0.5                               # normalización de SigLIP
            x = x.transpose(0, 3, 1, 2)
            try:
                y = self.sess.run(None, {self.input: x})[0]
            except Exception:
                if self.provider == "CPUExecutionProvider":
                    raise
                import onnxruntime as ort                     # la GPU falló a mitad: seguir en CPU
                self.sess = ort.InferenceSession(self.path, providers=["CPUExecutionProvider"])
                self.provider = "CPUExecutionProvider"
                y = self.sess.run(None, {self.input: x})[0]
            out.append(y)
        e = np.concatenate(out).astype(np.float32) if out else np.zeros((0, 1), np.float32)
        return e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-8)


def views(path, floor=False):
    """De un cuadro muestreado (448×448, video aplastado a cuadrado) sale una imagen de 224:
    el cuadro entero (locación, si se ve el piso) o la franja de abajo (la superficie)."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if floor:
        im = im.crop((0, int(h * 0.55), w, h))
    return np.asarray(im.resize((SIZE, SIZE), Image.BICUBIC))
