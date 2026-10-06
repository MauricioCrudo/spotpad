"""Modelo falso con la misma interfaz que el de verdad (para tests y para probar sin bajar SigLIP):
el «embedding» es el color medio en una grilla de 4×4. Sirve para escenas/locaciones; las superficies
salen sin sentido."""
import os

import numpy as np


def make(folder, dim_grid=4, seed=0):
    import onnx
    from onnx import TensorProto, helper
    os.makedirs(folder, exist_ok=True)
    k = 224 // dim_grid
    nodes = [helper.make_node("AveragePool", ["pixel_values"], ["p"], kernel_shape=[k, k], strides=[k, k]),
             helper.make_node("Flatten", ["p"], ["image_embeds"], axis=1)]
    g = helper.make_graph(nodes, "fake",
                          [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, ["b", 3, 224, 224])],
                          [helper.make_tensor_value_info("image_embeds", TensorProto.FLOAT, ["b", 3 * dim_grid ** 2])])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    m.ir_version = 8
    onnx.save(m, os.path.join(folder, "vision.onnx"))
    from surfaces import load_config, prompt_groups, prompts_hash
    cfg = load_config()
    rng = np.random.default_rng(seed)
    d = 3 * dim_grid ** 2
    out = {}
    for name, prompts in prompt_groups(cfg).items():
        e = rng.normal(size=(len(prompts), d)).astype(np.float32)
        out[name] = e / np.linalg.norm(e, axis=1, keepdims=True)
    out["logit_scale"] = np.array(10.0, np.float32)
    out["logit_bias"] = np.array(0.0, np.float32)
    out["hash"] = np.array(prompts_hash(cfg))
    np.savez(os.path.join(folder, "text_emb.npz"), **out)
    return folder
