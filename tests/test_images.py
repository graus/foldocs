"""Images: drawings and diagrams are told apart from photos, so a theme can fit them uncropped."""

import io
import random

import pytest
from PIL import Image, ImageDraw

from foldocs import images


def test_a_drawing_is_a_graphic_on_its_own_ground():
    im = Image.new("RGB", (600, 400), (233, 255, 190))  # a lime field
    draw = ImageDraw.Draw(im)
    draw.rectangle((50, 50, 550, 200), fill="white", outline="black", width=3)
    draw.line((60, 300, 540, 260), fill="black", width=4)
    assert images._graphic(im) == (True, (233, 255, 190))


def test_a_photo_is_not():
    rng = random.Random(1)
    im = Image.new("RGB", (300, 200))
    im.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(300 * 200)])
    assert images._graphic(im) == (False, None)


def test_a_transparent_graphic_is_judged_on_white(tmp_path):
    im = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((100, 50, 300, 250), fill=(232, 70, 30, 255))
    data = tmp_path / "logo.png"
    im.save(data)
    info = images.process(data.read_bytes(), tmp_path / "out", "logo")
    assert info["graphic"] and info["color"] == "#ffffff"


def test_a_transparent_image_has_nothing_painted_behind_it(tmp_path):
    im = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((100, 50, 300, 250), fill=(232, 70, 30, 255))
    data = tmp_path / "logo.png"
    im.save(data)
    info = images.process(data.read_bytes(), tmp_path / "out", "logo")
    assert info["transparent"]
    assert "style" not in images.attrs({**info, "base": "/media/"})
    with Image.open(tmp_path / "out" / "logo-400.webp") as out:
        assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0


def test_an_opaque_image_with_alpha_keeps_its_ground(tmp_path):
    im = Image.new("RGBA", (400, 300), (248, 243, 234, 255))  # a chart on a page color, alpha all 255
    ImageDraw.Draw(im).rectangle((50, 100, 350, 150), fill=(168, 71, 31, 255))
    data = tmp_path / "chart.png"
    im.save(data)
    info = images.process(data.read_bytes(), tmp_path / "out", "chart")
    assert not info["transparent"]
    assert images.attrs({**info, "base": "/media/"})["style"] == f"background-color:{info['color']}"


def test_an_image_too_large_to_hold_in_memory_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "PIXELS_MAX", 100)
    buf = io.BytesIO()
    Image.new("RGB", (20, 20)).save(buf, "PNG")
    with pytest.raises(ValueError, match="too large"):
        images.process(buf.getvalue(), tmp_path, "x")


def encoded(im: Image.Image, format: str, **params) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format, **params)
    return buf.getvalue()


def test_a_large_photo_is_worked_on_at_the_largest_size_shown(tmp_path):
    info = images.process(encoded(Image.new("RGB", (6000, 1000), "orange"), "JPEG"), tmp_path, "x")
    assert (info["width"], info["height"]) == (max(images.WIDTHS), 400)
    assert max(v["w"] for v in info["variants"]) == max(images.WIDTHS)


def test_a_turned_photo_is_upright_and_keeps_its_date(tmp_path):
    exif = Image.Exif()
    exif[0x0112], exif[0x0132] = 6, "2024:01:02 03:04:05"  # turned a quarter; when it was taken
    info = images.process(encoded(Image.new("RGB", (6000, 3000)), "JPEG", exif=exif), tmp_path, "x")
    assert (info["width"], info["height"]) == (2400, 4800)
    assert info["taken"] == "2024-01-02T03:04:05"


def test_a_tall_image_keeps_its_width(tmp_path):
    info = images.process(encoded(Image.new("RGB", (1440, 9000), "white"), "PNG"), tmp_path, "x")
    assert info["width"] == 1440 and max(v["w"] for v in info["variants"]) == 1440


def test_a_phone_photo_with_a_second_image_stays_a_photo(tmp_path):
    """Phones store a depth map or HDR layer as a second image in the JPEG (MPO): not a frame."""
    exif = Image.Exif()
    exif[0x0112] = 6
    data = encoded(
        Image.new("RGB", (2000, 1500)), "MPO", save_all=True, append_images=[Image.new("RGB", (2000, 1500))], exif=exif
    )
    info = images.process(data, tmp_path, "x")
    with Image.open(tmp_path / info["variants"][0]["file"]) as webp:
        assert webp.n_frames == 1
    assert (info["width"], info["height"]) == (1500, 2000)


def _gif_blocks(size: tuple[int, int], color: int) -> tuple[bytes, bytes]:
    """A one-frame GIF as its header and its image, to put frames of other sizes together."""
    data = encoded(Image.new("P", size, color), "GIF")
    flags = data[10]
    head = data[: 13 + (3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0)]
    rest = data[len(head) : -1]
    while rest[0] == 0x21:  # extensions before the image
        i = 2
        while rest[i]:
            i += rest[i] + 1
        rest = rest[i + 1 :]
    return head, rest


def test_an_animation_whose_frames_outgrow_it_is_refused(tmp_path):
    """A GIF says it is 10 by 10 pixels, then holds frames of 2000 by 2000: memory it hides."""
    head, small = _gif_blocks((10, 10), 1)
    _, big = _gif_blocks((2000, 2000), 2)
    delay = bytes([0x21, 0xF9, 4, 0, 10, 0, 0, 0])
    with pytest.raises(ValueError, match="outgrow"):
        images.process(head + delay + small + delay + big + b";", tmp_path, "x")


def test_an_animated_gif_stays_animated(tmp_path):
    frames = [Image.new("RGB", (40, 30), c) for c in ("red", "green", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=200, loop=0)
    info = images.process(buf.getvalue(), tmp_path, "x")
    with Image.open(tmp_path / info["variants"][0]["file"]) as webp:
        webp.load()
        assert webp.n_frames == 3 and webp.info["duration"] == 200
