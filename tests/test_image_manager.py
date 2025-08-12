import importlib
import builtins
import sys
import base64
import io
from PIL import Image


def _sample_png():
    img = Image.new("RGB", (1, 1), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_validate_with_imghdr():
    import image_manager
    importlib.reload(image_manager)
    data = _sample_png()
    result, err = image_manager.validate_and_process_image(data)
    assert err is None
    assert result.startswith("data:image/png;base64,")
    assert image_manager.imghdr is not None


def test_validate_without_imghdr(monkeypatch):
    orig_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "imghdr":
            raise ModuleNotFoundError
        return orig_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    if "image_manager" in sys.modules:
        del sys.modules["image_manager"]
    img_module = importlib.import_module("image_manager")

    data = _sample_png()
    result, err = img_module.validate_and_process_image(data)
    assert err is None
    assert result.startswith("data:image/png;base64,")
    assert img_module.imghdr is None
