"""Headless corporate branding persistence and minimal engineering logo generator.

This module MUST NEVER import PySide6.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from platformdirs import user_config_dir

from core.models import CorporateBrandingProfile

DEFAULT_BRANDING_PATH: Path = Path(user_config_dir("MetBalancePro")) / "branding_profile.json"
DEFAULT_LOGO_PATH: Path = Path(user_config_dir("MetBalancePro")) / "default_logo.png"


def _parse_hex_rgb(hex_color: str, fallback: tuple[int, int, int] = (27, 73, 101)) -> tuple[int, int, int]:
    """Parse #RRGGBB color string to an (R, G, B) tuple with safe fallback."""
    text = (hex_color or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return fallback
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return fallback


def load_branding_profile(path: Optional[Path] = None) -> CorporateBrandingProfile:
    """Load persisted CorporateBrandingProfile from JSON, falling back to defaults."""
    target = Path(path) if path is not None else DEFAULT_BRANDING_PATH
    if not target.exists():
        return CorporateBrandingProfile()
    try:
        raw = target.read_text(encoding="utf-8")
        return CorporateBrandingProfile.model_validate_json(raw)
    except Exception:
        return CorporateBrandingProfile()


def save_branding_profile(
    profile: CorporateBrandingProfile,
    path: Optional[Path] = None,
) -> Path:
    """Persist CorporateBrandingProfile as UTF-8 JSON and return the file path."""
    target = Path(path) if path is not None else DEFAULT_BRANDING_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return target


def _load_clean_font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Load Segoe UI or DejaVu Sans TrueType font with default bitmap fallback."""
    candidates = (
        ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def generate_default_corporate_logo(
    output_path: Optional[Path] = None,
    primary_color: str = "#1B4965",
) -> Path:
    """Generate a crisp minimal CAD-style engineering logo PNG via Pillow."""
    target = Path(output_path) if output_path is not None else DEFAULT_LOGO_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    width, height = 320, 96
    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    rgb = _parse_hex_rgb(primary_color)
    slate = (51, 65, 85)       # #334155
    muted = (100, 116, 139)    # #64748B
    border = (203, 213, 225)   # #CBD5E1

    # Technical frame border
    draw.rectangle([(1, 1), (width - 2, height - 2)], outline=border, width=1)
    # Left accent bar
    draw.rectangle([(1, 1), (8, height - 2)], fill=rgb)

    # Geometric technical balance / flow emblem (no clichés or emojis)
    cx, cy = 48, 48
    # Outer technical square-frame diamond
    draw.polygon([(cx, 16), (cx + 26, cy), (cx, 80), (cx - 26, cy)], outline=rgb, width=3)
    # Horizontal balance beam & fulcrum nodes
    draw.line([(cx - 18, cy), (cx + 18, cy)], fill=slate, width=2)
    draw.line([(cx, 26), (cx, 70)], fill=slate, width=2)
    draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill=rgb, outline=slate, width=1)
    draw.rectangle([(cx - 20, cy - 3), (cx - 14, cy + 3)], fill=rgb)
    draw.rectangle([(cx + 14, cy - 3), (cx + 20, cy + 3)], fill=rgb)

    # Clean engineering typography
    title_font = _load_clean_font(22, bold=True)
    sub_font = _load_clean_font(11, bold=False)
    draw.text((88, 22), "METBALANCE", fill=(30, 41, 59), font=title_font)
    draw.text((238, 22), "PRO", fill=rgb, font=title_font)
    draw.text((88, 54), "STATISTICAL MASS BALANCE", fill=muted, font=sub_font)

    img.save(target, format="PNG")
    return target


def resolve_corporate_logo(
    branding: CorporateBrandingProfile,
    fallback_path: Optional[Path] = None,
) -> Path:
    """Return a valid logo image path from branding, or generate the default corporate logo."""
    if branding.logo_path:
        candidate = Path(branding.logo_path)
        if candidate.is_file():
            return candidate
    return generate_default_corporate_logo(
        output_path=fallback_path,
        primary_color=branding.primary_color,
    )
