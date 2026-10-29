"""Responsive WebP variants of an image."""

import io
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence
from pillow_heif import register_heif_opener

register_heif_opener()  # iPhone photos

# Widths for the sizes images are shown at: a card or portrait on a phone (480, 720 at 2x), a
# photo page (1080, 1600), and a large screen at 2x (2400). Quality 75 with the slowest, best
# compression looks the same as 80 and is about a fifth smaller.
WIDTHS = (480, 720, 1080, 1600, 2400)
SIZES = "(min-width: 48rem) 42.5rem, calc(100vw - 2rem)"  # as wide as the text, unless a template says
QUALITY = 75
EXIF_IFD, EXIF_DATETIME_ORIGINAL, EXIF_DATETIME = 0x8769, 0x9003, 0x0132  # when taken; else last changed
EXIF_ORIENTATION = 0x0112  # 5 to 8: turned a quarter, so its width on screen is its height in the file
SRC_WIDTH = 960  # the variant in `src`, for a browser that ignores srcset
# Decoded, an image takes 3 or 4 bytes a pixel: one larger than this is refused rather than let it
# take the sync's memory. A JPEG is decoded at a fraction of its size first, so few photos are.
PIXELS_MAX = 50_000_000
ANIMATION_PIXELS_MAX = 20_000_000  # all its frames together; a larger animation is published still


def process(data: bytes, out_dir: Path, stem: str) -> dict:
    """Write `{stem}-{width}.webp` variants to out_dir (never wider than the original) and
    describe them. File names are relative to out_dir; the caller adds the URL prefix."""
    im = Image.open(io.BytesIO(data))
    taken = _taken(im)
    animation = _animation(im)
    im = animation[0][0] if animation else _still(im)
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if has_alpha else "RGB")
    # Only an image with pixels that let the page through, in any frame; many PNGs have alpha and
    # none of those.
    frames = animation[0] if animation else [im]
    transparent = has_alpha and any(f.getchannel("A").getextrema()[0] < 255 for f in frames)

    out_dir.mkdir(parents=True, exist_ok=True)
    variants = []
    for width in sorted({min(w, im.width) for w in WIDTHS}):
        size = (width, round(im.height * width / im.width))
        name = f"{stem}-{width}.webp"
        if animation:
            frames = [f if f.size == size else f.resize(size, Image.Resampling.LANCZOS) for f in animation[0]]
            frames[0].save(
                out_dir / name, "WEBP", save_all=True, append_images=frames[1:], duration=animation[1], loop=0,
                quality=QUALITY, method=4,
            )  # fmt: skip
        else:
            resized = im if size == im.size else im.resize(size, Image.Resampling.LANCZOS)
            resized.save(out_dir / name, "WEBP", quality=QUALITY, method=6)
        variants.append({"w": width, "file": name})

    # Judged as seen on a light page: transparent pixels are black underneath, and would read as such.
    seen = Image.alpha_composite(Image.new("RGBA", im.size, "white"), im).convert("RGB") if has_alpha else im
    graphic, ground = _graphic(seen)
    r, g, b = ground or seen.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    return {
        "width": im.width,
        "height": im.height,
        "variants": variants,
        # Shown while the image loads; for a graphic, also around it when it is fitted, not cropped.
        "color": f"#{r:02x}{g:02x}{b:02x}",
        "graphic": graphic,
        # The page shows through it, so nothing is painted behind it.
        "transparent": transparent,
        "taken": taken,
    }


def _still(im: Image.Image) -> Image.Image:
    """The image upright, decoded and resized to no wider on screen than the widest variant."""
    turned = im.getexif().get(EXIF_ORIENTATION) in (5, 6, 7, 8)
    width, height = (im.height, im.width) if turned else im.size  # as shown
    scale = min(1, max(WIDTHS) / width)
    shown = (max(1, round(width * scale)), max(1, round(height * scale)))
    size = shown[::-1] if turned else shown  # the same, as the file has it
    im.draft("RGB", size)  # a JPEG: decoded at 1/2, 1/4 or 1/8 if that is still large enough
    if im.width * im.height > PIXELS_MAX:
        raise ValueError(f"an image of {im.width}×{im.height} pixels: too large to publish")
    if im.mode == "P":  # resized smoothly, as a palette image isn't
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
    if im.size != size:
        im = im.resize(size, Image.Resampling.LANCZOS)
    return ImageOps.exif_transpose(im)


ANIMATED = ("GIF", "WEBP", "PNG")  # a JPEG's second image (a depth map, an HDR layer) is no frame


def _animation(im: Image.Image) -> tuple[list[Image.Image], list[int]] | None:
    """An animated GIF's (or WebP's) frames and how long each shows, in ms; None for a still image.
    Its variants are animated WebP, which every browser plays like a GIF. One too large is
    published still; one with a frame larger than the size it declares is refused."""
    if im.format not in ANIMATED or not getattr(im, "is_animated", False):
        return None
    if im.n_frames * im.width * im.height > ANIMATION_PIXELS_MAX:
        return None
    size, frames, durations = im.size, [], []
    for frame in ImageSequence.Iterator(im):
        if frame.size != size:  # it would take far more memory than its size says
            raise ValueError(f"an animation whose frames outgrow its {size[0]}×{size[1]} pixels")
        frames.append(frame.convert("RGBA"))
        durations.append(frame.info.get("duration") or 100)
    return frames, durations


GRAPHIC_SHARE = 0.6  # photos measure 0.2 to 0.3, drawings and diagrams 0.8 and up
SAMPLE = 96  # the image is judged at this size, in pixels a side


def _graphic(im: Image.Image) -> tuple[bool, tuple[int, int, int] | None]:
    """Whether the image is a drawing, diagram or slide rather than a photo, and its ground color.
    Graphics are made of a few flat colors: most of their pixels share a handful of them, which
    photos never do. A theme can fit a graphic in its frame where it would crop a photo."""
    small = im.convert("RGB").resize((SAMPLE, SAMPLE), Image.Resampling.NEAREST)
    counts = sorted(small.point(lambda v: v // 16 * 16).getcolors(SAMPLE * SAMPLE), reverse=True)
    if sum(n for n, _ in counts[:8]) / (SAMPLE * SAMPLE) < GRAPHIC_SHARE:
        return False, None
    # Its ground: the commonest color along its edges, so the space around it continues it.
    last = SAMPLE - 1
    edges = [small.getpixel((x, y)) for x in range(SAMPLE) for y in (0, last)]
    edges += [small.getpixel((x, y)) for y in range(SAMPLE) for x in (0, last)]
    return True, max(set(edges), key=edges.count)


def _taken(im: Image.Image) -> str | None:
    """EXIF capture time as an ISO string without time zone (cameras record local time)."""
    exif = im.getexif()
    raw = exif.get_ifd(EXIF_IFD).get(EXIF_DATETIME_ORIGINAL) or exif.get(EXIF_DATETIME)
    try:
        return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:
        return None


def attrs(info: dict, sizes: str = SIZES) -> dict:
    """<img> attributes for a processed image; `info["base"]` is the URL of its folder."""
    variants = info["variants"]
    src = min(variants, key=lambda v: abs(v["w"] - SRC_WIDTH))
    return {
        "src": info["base"] + src["file"],
        "srcset": ", ".join(f"{info['base']}{v['file']} {v['w']}w" for v in variants),
        "sizes": sizes,
        "width": info["width"],
        "height": info["height"],
        **({} if info.get("transparent") else {"style": f"background-color:{info['color']}"}),
        **({"data-graphic": ""} if info.get("graphic") else {}),
    }
