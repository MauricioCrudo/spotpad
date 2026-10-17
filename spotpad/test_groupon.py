"""group-on: si Pro Tools agrupa pero no deja el clip seleccionado, se vuelve a marcar el rango y se reintenta."""
import os, tempfile
os.environ["SPOTPAD_DATA"] = tempfile.mkdtemp()
import bridge, edicion
from types import SimpleNamespace as N


class Eng:
    def __init__(self, fallas):
        self.fallas, self.renames, self.marcas = fallas, [], 0
    def select_tracks_by_name(self, names): pass
    def set_timeline_selection(self, **k): self.marcas += 1
    def group_clips(self): pass
    def rename_selected_clip(self, new_name, **k):
        if self.fallas:
            self.fallas -= 1
            raise Exception("ErrType 126: PT_InvalidParameter (No clip is selected)")
        self.renames.append(new_name)


class PT(bridge.ProTools):
    def _selection_samples(self, e): return (100, 200)


bridge.time.sleep = lambda s: None
pt = PT()
e = Eng(fallas=1)
r = pt._group_on(e, N(name="Hands Body"), "Hands skin")
assert r["ok"] and e.renames == ["Hands skin"] and e.marcas >= 1, (r, e.renames)
e = Eng(fallas=5)
try:
    pt._group_on(e, N(name="Hands Body"), "Hands skin"); raise AssertionError
except bridge.SpotError as ex:
    assert "renombralo" in str(ex)

# Pro Tools < 2025: el bounce de Regrabar va con los nombres de enum viejos
assert edicion.ptsl_viejo(N(ptsl_version=lambda: 5)) and not edicion.ptsl_viejo(N(ptsl_version=lambda: 2026))
j = edicion.nombres_viejos('{"source_type": "EMSType_Bus", "file_type": "EMFType_WAV", "offline_bounce": "TB_True"}')
assert '"Bus"' in j and '"EM_WAV"' in j and '"TB_True"' in j, j
print("group-on OK")
