"""Generate minimalist monochromatic 1.5px stroke (#334155) technical SVGs.

Generates 29 UI control icons (including 12x12 dropdown chevrons) and 7 ISO 10628 / P&ID
metallurgical process symbols into `metbalance_pro/app/assets/icons/`.
"""

from __future__ import annotations

from pathlib import Path


STROKE_COLOR = "#334155"
STROKE_WIDTH = "1.5"


def _wrap_svg(body: str, size: int = 24, stroke: str = STROKE_COLOR) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" fill="none" stroke="{stroke}" '
        f'stroke-width="{STROKE_WIDTH}" stroke-linecap="round" stroke-linejoin="round">\n'
        f"{body}\n"
        "</svg>\n"
    )


ICONS: dict[str, str] = {
    # --- 18 UI Controls ---
    "folder.svg": _wrap_svg(
        '  <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.2l1.8 2H19.5A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/>'
    ),
    "import_table.svg": _wrap_svg(
        '  <rect x="3" y="4" width="18" height="16" rx="1.5"/>\n'
        '  <line x1="3" y1="9" x2="21" y2="9"/>\n'
        '  <line x1="9" y1="9" x2="9" y2="20"/>\n'
        '  <line x1="15" y1="9" x2="15" y2="20"/>\n'
        '  <line x1="3" y1="14.5" x2="21" y2="14.5"/>\n'
        '  <polyline points="12 2 12 6.5"/>\n'
        '  <polyline points="10 4.5 12 6.5 14 4.5"/>'
    ),
    "gear.svg": _wrap_svg(
        '  <circle cx="12" cy="12" r="3.2"/>\n'
        '  <path d="M12 2.5v2.2M12 19.3v2.2M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.9 19.1l1.6-1.6M17.5 6.5l1.6-1.6"/>\n'
        '  <circle cx="12" cy="12" r="7.2" stroke-dasharray="2.6 2.1"/>'
    ),
    "matrix.svg": _wrap_svg(
        '  <polyline points="6 4 4 4 4 20 6 20"/>\n'
        '  <polyline points="18 4 20 4 20 20 18 20"/>\n'
        '  <rect x="7.5" y="7" width="2.2" height="2.2"/>\n'
        '  <rect x="14.3" y="7" width="2.2" height="2.2"/>\n'
        '  <rect x="7.5" y="14.8" width="2.2" height="2.2"/>\n'
        '  <rect x="14.3" y="14.8" width="2.2" height="2.2"/>\n'
        '  <line x1="11.2" y1="12" x2="12.8" y2="12"/>'
    ),
    "play.svg": _wrap_svg(
        '  <polygon points="6.5 4.5 19.5 12 6.5 19.5 6.5 4.5"/>'
    ),
    "alert_triangle.svg": _wrap_svg(
        '  <path d="M12 3.5L2.8 19.5h18.4z"/>\n'
        '  <line x1="12" y1="9.5" x2="12" y2="14"/>\n'
        '  <line x1="12" y1="16.8" x2="12.01" y2="16.8"/>'
    ),
    "check_circle.svg": _wrap_svg(
        '  <circle cx="12" cy="12" r="9"/>\n'
        '  <polyline points="8 12.2 10.8 15 16.2 9.2"/>'
    ),
    "scatter_plot.svg": _wrap_svg(
        '  <polyline points="4 4 4 20 20 20"/>\n'
        '  <line x1="5.5" y1="18.5" x2="19" y2="5" stroke-dasharray="2 2"/>\n'
        '  <circle cx="8" cy="15" r="1.3"/>\n'
        '  <circle cx="11.5" cy="12" r="1.3"/>\n'
        '  <circle cx="15" cy="9.5" r="1.3"/>\n'
        '  <circle cx="17.5" cy="7" r="1.3"/>'
    ),
    "export_doc.svg": _wrap_svg(
        '  <path d="M6 3h8l4 4v14H6z"/>\n'
        '  <polyline points="14 3 14 7 18 7"/>\n'
        '  <line x1="12" y1="17" x2="12" y2="10.5"/>\n'
        '  <polyline points="9.5 13 12 10.5 14.5 13"/>'
    ),
    "language.svg": _wrap_svg(
        '  <circle cx="12" cy="12" r="9"/>\n'
        '  <ellipse cx="12" cy="12" rx="4" ry="9"/>\n'
        '  <line x1="3.5" y1="9" x2="20.5" y2="9"/>\n'
        '  <line x1="3.5" y1="15" x2="20.5" y2="15"/>'
    ),
    "theme.svg": _wrap_svg(
        '  <circle cx="12" cy="12" r="8.5"/>\n'
        '  <line x1="12" y1="3.5" x2="12" y2="20.5"/>\n'
        '  <line x1="12" y1="8" x2="17.5" y2="8"/>\n'
        '  <line x1="12" y1="12" x2="20.2" y2="12"/>\n'
        '  <line x1="12" y1="16" x2="17.5" y2="16"/>'
    ),
    "reset_layout.svg": _wrap_svg(
        '  <rect x="3.5" y="4" width="17" height="16" rx="1.5"/>\n'
        '  <line x1="9" y1="4" x2="9" y2="20"/>\n'
        '  <line x1="9" y1="14" x2="20.5" y2="14"/>\n'
        '  <polyline points="13 9 15 7 17 9"/>'
    ),
    "plus.svg": _wrap_svg(
        '  <line x1="12" y1="5" x2="12" y2="19"/>\n'
        '  <line x1="5" y1="12" x2="19" y2="12"/>'
    ),
    "trash.svg": _wrap_svg(
        '  <polyline points="4 6.5 20 6.5"/>\n'
        '  <path d="M9.5 6.5V4.5h5v2"/>\n'
        '  <path d="M6.5 6.5l1 13h9l1-13"/>\n'
        '  <line x1="10" y1="10" x2="10" y2="16.5"/>\n'
        '  <line x1="14" y1="10" x2="14" y2="16.5"/>'
    ),
    "link_stream.svg": _wrap_svg(
        '  <circle cx="5.5" cy="12" r="2.5"/>\n'
        '  <circle cx="18.5" cy="12" r="2.5"/>\n'
        '  <line x1="8" y1="12" x2="15.5" y2="12"/>\n'
        '  <polyline points="13 9.5 15.8 12 13 14.5"/>'
    ),
    "lock.svg": _wrap_svg(
        '  <rect x="5" y="10.5" width="14" height="9.5" rx="1.5"/>\n'
        '  <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>\n'
        '  <line x1="12" y1="14" x2="12" y2="16.5"/>'
    ),
    "pdf_doc.svg": _wrap_svg(
        '  <path d="M5.5 3h8.5l4.5 4.5V21h-13z"/>\n'
        '  <polyline points="14 3 14 7.5 18.5 7.5"/>\n'
        '  <line x1="8.5" y1="11.5" x2="15.5" y2="11.5"/>\n'
        '  <line x1="8.5" y1="14.5" x2="15.5" y2="14.5"/>\n'
        '  <rect x="8.5" y="17" width="7" height="2"/>'
    ),
    "excel_doc.svg": _wrap_svg(
        '  <path d="M5.5 3h8.5l4.5 4.5V21h-13z"/>\n'
        '  <polyline points="14 3 14 7.5 18.5 7.5"/>\n'
        '  <rect x="8" y="10.5" width="8" height="7.5"/>\n'
        '  <line x1="12" y1="10.5" x2="12" y2="18"/>\n'
        '  <line x1="8" y1="14.2" x2="16" y2="14.2"/>'
    ),
    "chevron_down.svg": _wrap_svg(
        '  <polyline points="2.5 4.2 6 7.8 9.5 4.2"/>',
        size=12,
        stroke="#334155",
    ),
    "chevron_down_dark.svg": _wrap_svg(
        '  <polyline points="2.5 4.2 6 7.8 9.5 4.2"/>',
        size=12,
        stroke="#CBD5E1",
    ),
    # --- 7 ISO 10628 / P&ID Process Symbols ---
    "pid_mixer_splitter.svg": _wrap_svg(
        '  <circle cx="12" cy="12" r="5"/>\n'
        '  <line x1="2" y1="12" x2="7" y2="12"/>\n'
        '  <line x1="17" y1="12" x2="22" y2="12"/>\n'
        '  <line x1="12" y1="2" x2="12" y2="7"/>\n'
        '  <line x1="12" y1="17" x2="12" y2="22"/>\n'
        '  <circle cx="12" cy="12" r="1.2"/>'
    ),
    "pid_flotation_cell.svg": _wrap_svg(
        '  <polygon points="5 7 19 7 17.5 19 6.5 19"/>\n'
        '  <line x1="12" y1="3" x2="12" y2="16"/>\n'
        '  <line x1="9" y1="16" x2="15" y2="16"/>\n'
        '  <line x1="9.5" y1="3" x2="14.5" y2="3"/>\n'
        '  <polyline points="19 8.5 22 8.5 22 11"/>\n'
        '  <line x1="2" y1="12" x2="5.5" y2="12"/>'
    ),
    "pid_concentrator.svg": _wrap_svg(
        '  <rect x="5" y="4.5" width="14" height="15"/>\n'
        '  <line x1="5" y1="19.5" x2="19" y2="4.5"/>\n'
        '  <line x1="2" y1="12" x2="5" y2="12"/>\n'
        '  <line x1="19" y1="8.5" x2="22" y2="8.5"/>\n'
        '  <line x1="19" y1="15.5" x2="22" y2="15.5"/>'
    ),
    "pid_rougher.svg": _wrap_svg(
        '  <rect x="4" y="7" width="7.5" height="11"/>\n'
        '  <rect x="12.5" y="7" width="7.5" height="11"/>\n'
        '  <line x1="7.75" y1="4" x2="7.75" y2="14.5"/>\n'
        '  <line x1="5.75" y1="14.5" x2="9.75" y2="14.5"/>\n'
        '  <line x1="16.25" y1="4" x2="16.25" y2="14.5"/>\n'
        '  <line x1="14.25" y1="14.5" x2="18.25" y2="14.5"/>\n'
        '  <line x1="2" y1="12" x2="4" y2="12"/>\n'
        '  <line x1="20" y1="9" x2="22" y2="9"/>\n'
        '  <line x1="20" y1="15" x2="22" y2="15"/>'
    ),
    "pid_scavenger.svg": _wrap_svg(
        '  <polygon points="4.5 7.5 17.5 7.5 16 18 6 18"/>\n'
        '  <line x1="11" y1="4" x2="11" y2="15"/>\n'
        '  <line x1="8.5" y1="15" x2="13.5" y2="15"/>\n'
        '  <polyline points="17.5 9 21 9 21 4.5 6 4.5"/>\n'
        '  <polyline points="8 3 6 4.5 8 6"/>\n'
        '  <line x1="17" y1="15.5" x2="21.5" y2="15.5"/>'
    ),
    "pid_cleaner.svg": _wrap_svg(
        '  <rect x="7" y="5" width="10" height="15"/>\n'
        '  <line x1="7" y1="10" x2="17" y2="10" stroke-dasharray="2 1.5"/>\n'
        '  <line x1="12" y1="5" x2="12" y2="2"/>\n'
        '  <polyline points="10 3.5 12 2 14 3.5"/>\n'
        '  <line x1="3" y1="13" x2="7" y2="13"/>\n'
        '  <line x1="17" y1="17" x2="21" y2="17"/>'
    ),
    "pid_flow_arrow.svg": _wrap_svg(
        '  <line x1="2.5" y1="12" x2="19.5" y2="12"/>\n'
        '  <polygon points="21.5 12 15.5 8.5 15.5 15.5"/>\n'
        '  <line x1="6" y1="9.5" x2="6" y2="14.5"/>'
    ),
    # --- 9 Canvas-First UX & Collapsible Dock Icons ---
    "pin.svg": _wrap_svg(
        '  <line x1="8" y1="3.5" x2="16" y2="3.5"/>\n'
        '  <path d="M9.5 3.5V9L7 13.5h10L14.5 9V3.5"/>\n'
        '  <line x1="12" y1="13.5" x2="12" y2="21"/>'
    ),
    "unpin.svg": _wrap_svg(
        '  <line x1="3.5" y1="3.5" x2="20.5" y2="20.5"/>\n'
        '  <line x1="9" y1="4" x2="16" y2="4"/>\n'
        '  <path d="M9.5 4v2.5"/>\n'
        '  <path d="M14.5 4v5.5l2.5 4h-2"/>\n'
        '  <path d="M8.5 11.5L7 13.5h4"/>\n'
        '  <line x1="12" y1="15" x2="12" y2="20.5"/>'
    ),
    "cursor_select.svg": _wrap_svg(
        '  <polygon points="5.5 3.5 5.5 18.5 9.8 14.5 13.2 20.5 15.6 19.3 12.2 13.5 18.5 13.5"/>'
    ),
    "block_feed.svg": _wrap_svg(
        '  <rect x="3.5" y="6" width="13" height="12" rx="2.5"/>\n'
        '  <polyline points="8.5 9.5 11.5 12 8.5 14.5"/>\n'
        '  <line x1="16.5" y1="12" x2="18.7" y2="12"/>\n'
        '  <circle cx="20" cy="12" r="1.3"/>'
    ),
    "block_process.svg": _wrap_svg(
        '  <circle cx="3.8" cy="12" r="1.3"/>\n'
        '  <line x1="5.1" y1="12" x2="7" y2="12"/>\n'
        '  <rect x="7" y="5.5" width="10" height="13" rx="1.5"/>\n'
        '  <line x1="9.5" y1="12" x2="14.5" y2="12"/>\n'
        '  <line x1="17" y1="9" x2="18.9" y2="9"/>\n'
        '  <circle cx="20.2" cy="9" r="1.3"/>\n'
        '  <line x1="17" y1="15" x2="18.9" y2="15"/>\n'
        '  <circle cx="20.2" cy="15" r="1.3"/>'
    ),
    "block_product.svg": _wrap_svg(
        '  <circle cx="4" cy="12" r="1.3"/>\n'
        '  <line x1="5.3" y1="12" x2="7.5" y2="12"/>\n'
        '  <rect x="7.5" y="6" width="13" height="12" rx="2.5"/>\n'
        '  <polyline points="12.5 9.5 15.5 12 12.5 14.5"/>'
    ),
    "undo.svg": _wrap_svg(
        '  <polyline points="8.5 5.5 4.5 9.5 8.5 13.5"/>\n'
        '  <path d="M4.5 9.5h9.5a5 5 0 0 1 0 10H9"/>'
    ),
    "redo.svg": _wrap_svg(
        '  <polyline points="15.5 5.5 19.5 9.5 15.5 13.5"/>\n'
        '  <path d="M19.5 9.5H10a5 5 0 0 0 0 10h5"/>'
    ),
    "chevron_right.svg": _wrap_svg(
        '  <polyline points="9 6 15 12 9 18"/>'
    ),
}


def generate_all_icons(output_dir: Path | None = None) -> list[Path]:
    """Write all 36 SVG assets to disk and return their file paths."""
    target_dir = output_dir or (
        Path(__file__).resolve().parent.parent / "app" / "assets" / "icons"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    for filename, svg_content in ICONS.items():
        path = target_dir / filename
        path.write_text(svg_content, encoding="utf-8")
        generated.append(path)
    return generated


if __name__ == "__main__":
    paths = generate_all_icons()
    print(f"Generated {len(paths)} SVG icons in {paths[0].parent}")
