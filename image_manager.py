import os
import base64
from typing import Tuple, Optional

try:
    import imghdr
except ModuleNotFoundError:  # pragma: no cover - fallback for stripped stdlib
    imghdr = None
    from PIL import Image
    import io


def validate_and_process_image(contents: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate base64 image data and return processed string."""
    if not contents:
        return None, "No image data provided"
    if "," not in contents:
        return None, "Invalid image data"
    header, encoded = contents.split(",", 1)
    try:
        data = base64.b64decode(encoded)
    except Exception:
        return None, "Invalid base64 encoding"
    if imghdr is not None:
        img_type = imghdr.what(None, h=data)
    else:
        try:
            img = Image.open(io.BytesIO(data))
            img_type = img.format.lower() if img.format else None
        except Exception:
            img_type = None
    if not img_type:
        return None, "Unsupported image type"
    processed = f"data:image/{img_type};base64,{base64.b64encode(data).decode()}"
    return processed, None


def cache_image(image_data: str, path: str = "data/custom_image.txt") -> Tuple[bool, Optional[str]]:
    """Cache processed image data to disk."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(image_data)
        return True, None
    except Exception as e:
        return False, str(e)
