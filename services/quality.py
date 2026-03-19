import base64
import io
from PIL import Image


def _is_svg(image_bytes: bytes) -> bool:
    try:
        snippet = image_bytes[:512].decode("utf-8", errors="ignore").lower()
        return "<svg" in snippet
    except Exception:
        return False


def _svg_assessment(image_bytes: bytes) -> dict:
    """Return a quality result for SVG without rendering it."""
    b64 = base64.b64encode(image_bytes).decode()
    thumbnail_b64 = f"data:image/svg+xml;base64,{b64}"
    file_size_kb = len(image_bytes) / 1024
    return {
        "reachable": True,
        "width": 500,   # SVG is resolution-independent; treat as large
        "height": 500,
        "format": "SVG",
        "has_transparency": True,  # SVG is inherently transparent
        "file_size_kb": round(file_size_kb, 1),
        "quality_score": 90,
        "quality_label": "Excellent",
        "thumbnail_b64": thumbnail_b64,
    }


def assess_image(image_bytes: bytes, url: str) -> dict:
    if _is_svg(image_bytes):
        return _svg_assessment(image_bytes)
    try:
        img = Image.open(io.BytesIO(image_bytes))

        # For ICO: pick the largest sub-image
        if hasattr(img, "n_frames") or img.format == "ICO":
            try:
                sizes = img.info.get("sizes", [])
                if sizes:
                    img.size = max(sizes)
            except Exception:
                pass

        img_format = img.format or _guess_format_from_url(url)
        width, height = img.size

        # Convert to RGBA for transparency check
        if img.mode != "RGBA":
            rgba = img.convert("RGBA")
        else:
            rgba = img

        has_transparency = _has_real_transparency(rgba)
        file_size_kb = len(image_bytes) / 1024

        # Generate thumbnail
        thumb = img.copy()
        thumb.thumbnail((120, 120), Image.LANCZOS)
        if thumb.mode not in ("RGB", "RGBA"):
            thumb = thumb.convert("RGBA")
        buf = io.BytesIO()
        thumb.save(buf, format="PNG")
        thumbnail_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        score = _score(width, height, img_format, has_transparency, file_size_kb)
        label = _label(score)

        return {
            "reachable": True,
            "width": width,
            "height": height,
            "format": img_format,
            "has_transparency": has_transparency,
            "file_size_kb": round(file_size_kb, 1),
            "quality_score": score,
            "quality_label": label,
            "thumbnail_b64": thumbnail_b64,
        }
    except Exception:
        return _unreachable()


def _has_real_transparency(rgba_img: Image.Image) -> bool:
    try:
        alpha = rgba_img.getchannel("A")
        min_alpha, max_alpha = alpha.getextrema()
        return min_alpha < 255
    except Exception:
        return False


def _guess_format_from_url(url: str) -> str:
    url_lower = url.lower().split("?")[0]
    if url_lower.endswith(".svg"):
        return "SVG"
    if url_lower.endswith(".png"):
        return "PNG"
    if url_lower.endswith(".jpg") or url_lower.endswith(".jpeg"):
        return "JPEG"
    if url_lower.endswith(".webp"):
        return "WEBP"
    if url_lower.endswith(".ico"):
        return "ICO"
    return "PNG"


def _score(width: int, height: int, fmt: str, has_transparency: bool, file_size_kb: float) -> int:
    score = 0

    # Resolution (max 40)
    pixels = width * height
    if pixels >= 400 * 400:
        score += 40
    elif pixels >= 200 * 200:
        score += 30
    elif pixels >= 100 * 100:
        score += 15
    elif pixels >= 32 * 32:
        score += 5

    # Format (max 20)
    fmt_upper = (fmt or "").upper()
    if fmt_upper in ("PNG", "SVG"):
        score += 20
    elif fmt_upper == "WEBP":
        score += 15
    elif fmt_upper == "JPEG":
        score += 10
    elif fmt_upper == "ICO":
        score += 5

    # Transparency (max 20)
    if has_transparency:
        score += 20

    # Aspect ratio (max 10)
    if width > 0 and height > 0:
        ratio = max(width, height) / min(width, height)
        if ratio <= 2.0:
            score += 10
        elif ratio <= 4.0:
            score += 5

    # File size sanity (max 10)
    if 5 <= file_size_kb <= 500:
        score += 10
    elif file_size_kb < 5:
        score += 2
    else:
        score += 5

    return min(score, 100)


def _label(score: int) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Good"
    if score >= 40:
        return "Fair"
    if score >= 20:
        return "Poor"
    return "Bad"


def _unreachable() -> dict:
    return {
        "reachable": False,
        "width": None,
        "height": None,
        "format": None,
        "has_transparency": False,
        "file_size_kb": None,
        "quality_score": 0,
        "quality_label": "Bad",
        "thumbnail_b64": None,
    }
