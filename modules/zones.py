# modules/zones.py
import json
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple, List

from PIL import Image, ImageDraw, ImageFont, ImageColor

# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

ZONES_FILE = DATA_DIR / "zones.json"

# ✅ en tu app los planos están en "modules/planos"
PLANOS_DIR = Path("modules/planos")
COLORED_DIR = Path("planos_coloreados")
PLANOS_DIR.mkdir(parents=True, exist_ok=True)
COLORED_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------
# IO (persistir zonas)
# ---------------------------------------------------------
def load_zones() -> dict:
    if not ZONES_FILE.exists():
        return {}
    try:
        with open(ZONES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_zones(data: dict) -> bool:
    try:
        with open(ZONES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def _safe_int(x, default=0) -> int:
    try:
        return int(round(float(str(x).replace(",", "."))))
    except Exception:
        return default


def _safe_float(x, default=0.0) -> float:
    try:
        return float(str(x).replace(",", "."))
    except Exception:
        return default


def _normalize_piso_label(piso: str) -> str:
    s = str(piso or "").strip()
    if not s:
        return "Piso 1"
    if s.lower().startswith("piso"):
        return "Piso " + s[4:].strip()
    m = re.findall(r"\d+", s)
    return f"Piso {m[0]}" if m else s


def _piso_num_from_label(piso_label: str) -> str:
    s = str(piso_label or "").strip()
    m = re.findall(r"\d+", s)
    return m[0] if m else "1"


def _rgba_from_any(color: str, default=(0, 160, 74, 90)) -> Tuple[int, int, int, int]:
    """
    Acepta:
      - "rgba(r,g,b,a)" con a en [0..1] o [0..255]
      - "#RRGGBB" / nombres ("red") / etc
    Devuelve RGBA con alpha 0..255
    """
    try:
        c = str(color or "").strip()
        if not c:
            return default

        if c.lower().startswith("rgba"):
            inside = c[c.find("(") + 1 : c.rfind(")")]
            parts = [p.strip() for p in inside.split(",")]
            if len(parts) >= 4:
                r = int(float(parts[0]))
                g = int(float(parts[1]))
                b = int(float(parts[2]))
                a_raw = float(parts[3])
                a = int(round(a_raw * 255)) if a_raw <= 1.0 else int(round(a_raw))
                a = max(0, min(255, a))
                return (r, g, b, a)

        r, g, b = ImageColor.getrgb(c)
        return (r, g, b, default[3])
    except Exception:
        return default


def _get_font(font_name: str, size: int) -> ImageFont.ImageFont:
    size = max(8, int(size or 12))
    candidates = []
    if font_name:
        candidates.append(str(font_name))
    candidates.extend(["DejaVuSans.ttf", "Arial.ttf", "arial.ttf"])
    for fn in candidates:
        try:
            return ImageFont.truetype(fn, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    if not text:
        return (0, 0)
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return (box[2] - box[0], box[3] - box[1])
    except Exception:
        return (len(text) * 7, 12)


# ---------------------------------------------------------
# Planos: buscar imagen del piso
# ---------------------------------------------------------
def _list_plan_images() -> List[Path]:
    patterns = ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.PNG", "*.JPG", "*.JPEG", "*.WEBP"]
    imgs: List[Path] = []
    for pat in patterns:
        imgs.extend(sorted(PLANOS_DIR.glob(pat)))
    return imgs


def _find_plan_path_by_piso_label(piso_label: str) -> Optional[Path]:
    """
    Heurística:
      - si el nombre del archivo contiene el número del piso → elige ese
      - si no, devuelve el primero
    """
    imgs = _list_plan_images()
    if not imgs:
        return None

    piso_num = _piso_num_from_label(piso_label)
    hit = next((p for p in imgs if re.search(rf"\b{re.escape(piso_num)}\b", p.stem)), None)
    if hit:
        return hit
    hit2 = next((p for p in imgs if piso_num in p.stem), None)
    return hit2 or imgs[0]


# ---------------------------------------------------------
# Fabric.js (streamlit-drawable-canvas) → lista de "shapes"
# ---------------------------------------------------------
def _fabric_objects(zones_json: dict) -> List[dict]:
    if not zones_json or not isinstance(zones_json, dict):
        return []
    objs = zones_json.get("objects")
    return objs if isinstance(objs, list) else []


def _extract_shapes_from_fabric(zones_json: dict) -> List[dict]:
    """
    Devuelve shapes normalizados:
      rect / circle / triangle (lo que el canvas usa)
    """
    out: List[dict] = []
    for o in _fabric_objects(zones_json):
        t = str(o.get("type", "")).lower()
        left = float(o.get("left", 0) or 0)
        top = float(o.get("top", 0) or 0)

        fill = _rgba_from_any(o.get("fill"), default=(0, 160, 74, 90))
        stroke = _rgba_from_any(o.get("stroke"), default=(0, 0, 0, 140))
        stroke_width = _safe_int(o.get("strokeWidth", 2), 2)

        if t == "rect":
            w = float(o.get("width", 0) or 0) * float(o.get("scaleX", 1) or 1)
            h = float(o.get("height", 0) or 0) * float(o.get("scaleY", 1) or 1)
            out.append({
                "type": "rect",
                "left": left,
                "top": top,
                "width": w,
                "height": h,
                "fill_rgba": fill,
                "stroke_rgba": stroke,
                "stroke_width": stroke_width,
            })

        elif t == "circle":
            r = float(o.get("radius", 0) or 0)
            sx = float(o.get("scaleX", 1) or 1)
            sy = float(o.get("scaleY", 1) or 1)
            out.append({
                "type": "circle",
                "left": left,
                "top": top,
                "radius_x": r * sx,
                "radius_y": r * sy,
                "fill_rgba": fill,
                "stroke_rgba": stroke,
                "stroke_width": stroke_width,
            })

        elif t == "triangle":
            w = float(o.get("width", 0) or 0) * float(o.get("scaleX", 1) or 1)
            h = float(o.get("height", 0) or 0) * float(o.get("scaleY", 1) or 1)
            out.append({
                "type": "triangle",
                "left": left,
                "top": top,
                "width": w,
                "height": h,
                "fill_rgba": fill,
                "stroke_rgba": stroke,
                "stroke_width": stroke_width,
            })

    return out


# ---------------------------------------------------------
# Título overlay (simple)
# ---------------------------------------------------------
def _draw_title_overlay(img: Image.Image, title: str, font_name: str = "DejaVuSans.ttf", font_size: int = 28) -> Image.Image:
    if not title:
        return img

    img = img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    font = _get_font(font_name, int(font_size or 28))
    tw, th = _text_size(draw, title, font)
    pad = 16

    box_w = min(img.size[0] - 2 * pad, tw + 2 * pad)
    box_h = th + 2 * pad
    x0 = (img.size[0] - box_w) // 2
    y0 = pad
    x1 = x0 + box_w
    y1 = y0 + box_h

    draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=(255, 255, 255, 180), outline=(0, 0, 0, 40), width=2)
    tx = x0 + (box_w - tw) // 2
    ty = y0 + (box_h - th) // 2
    draw.text((tx, ty), title, font=font, fill=(0, 0, 0, 230))

    return Image.alpha_composite(img, overlay)


# ---------------------------------------------------------
# Public API: render del plano con zonas
# ---------------------------------------------------------
def generate_colored_plan(
    base_image_path: str,
    zones_json: dict,
    title: Optional[str] = None,
    title_font: str = "DejaVuSans.ttf",
    title_size: int = 28,
) -> Image.Image:
    """
    Devuelve una PIL.Image:
      - base (plano) + overlay (formas transparentes)
      - título opcional
    """
    if not base_image_path:
        raise ValueError("base_image_path vacío")

    p = Path(str(base_image_path))
    if not p.exists():
        raise FileNotFoundError(f"No existe el plano: {p}")

    base = Image.open(p).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    shapes = _extract_shapes_from_fabric(zones_json)

    for s in shapes:
        t = s["type"]
        sw = int(s.get("stroke_width", 2))

        if t == "rect":
            x0 = int(round(s["left"]))
            y0 = int(round(s["top"]))
            x1 = int(round(s["left"] + s["width"]))
            y1 = int(round(s["top"] + s["height"]))
            if x1 <= x0 or y1 <= y0:
                continue
            draw.rectangle([x0, y0, x1, y1], fill=s["fill_rgba"], outline=s["stroke_rgba"], width=sw)

        elif t == "circle":
            rx = float(s.get("radius_x", 0))
            ry = float(s.get("radius_y", 0))
            x0 = int(round(s["left"]))
            y0 = int(round(s["top"]))
            x1 = int(round(s["left"] + 2 * rx))
            y1 = int(round(s["top"] + 2 * ry))
            if x1 <= x0 or y1 <= y0:
                continue
            draw.ellipse([x0, y0, x1, y1], fill=s["fill_rgba"], outline=s["stroke_rgba"], width=sw)

        elif t == "triangle":
            x0 = float(s["left"])
            y0 = float(s["top"])
            w = float(s["width"])
            h = float(s["height"])
            if w <= 0 or h <= 0:
                continue
            p1 = (int(round(x0 + w / 2)), int(round(y0)))
            p2 = (int(round(x0)), int(round(y0 + h)))
            p3 = (int(round(x0 + w)), int(round(y0 + h)))
            draw.polygon([p1, p2, p3], fill=s["fill_rgba"], outline=s["stroke_rgba"])
            if sw > 1:
                draw.line([p1, p2, p3, p1], fill=s["stroke_rgba"], width=sw)

    out = Image.alpha_composite(base, overlay)

    if title:
        out = _draw_title_overlay(out, title=str(title), font_name=title_font, font_size=int(title_size or 28))

    return out.convert("RGB")
