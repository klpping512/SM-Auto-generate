#!/usr/bin/env python3
"""Generate PNG icons for SA-LogiFlow in multiple sizes."""
import os
from PIL import Image, ImageDraw

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "icons")
SIZES = [16, 32, 180, 512]
BG = (99, 102, 241)       # #6366f1
FG = (255, 255, 255)      # #FFFFFF


def draw_truck(img: Image.Image) -> None:
    """Draw a flat, modern truck silhouette on the given image."""
    w, h = img.size
    draw = ImageDraw.Draw(img)

    # Coordinates are computed as fractions of canvas size for scalability
    # Cargo body: a rounded rectangle on the left
    cx, cy = w / 2, h / 2

    # --- cargo body ---
    bw = int(w * 0.42)  # body width
    bh = int(h * 0.32)  # body height
    bx = int(cx - bw * 0.68)
    by = int(cy - bh * 0.15)
    r = max(2, int(w * 0.04))
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=r, fill=FG)

    # --- cab ---
    cw = int(w * 0.26)
    ch = int(h * 0.26)
    cab_x = bx + bw - int(w * 0.02)
    cab_y = by + int(bh * 0.2)
    cab_r = max(1, int(w * 0.035))
    draw.rounded_rectangle([cab_x, cab_y, cab_x + cw, cab_y + ch], radius=cab_r, fill=FG)

    # --- cab window (indigo cutout) ---
    win_margin = max(2, int(w * 0.03))
    win_x = cab_x + win_margin
    win_y = cab_y + win_margin
    win_w = int(cw * 0.65)
    win_h = int(ch * 0.48)
    draw.rounded_rectangle([win_x, win_y, win_x + win_w, win_y + win_h],
                           radius=max(1, int(w * 0.015)), fill=BG)

    # --- wheels ---
    wheel_r = max(2, int(h * 0.085))
    hub_r = max(1, int(wheel_r * 0.4))

    # rear wheel
    wrx = bx + int(bw * 0.28)
    wry = by + bh - 1
    draw.ellipse([wrx - wheel_r, wry - wheel_r, wrx + wheel_r, wry + wheel_r], fill=FG)
    draw.ellipse([wrx - hub_r, wry - hub_r, wrx + hub_r, wry + hub_r], fill=BG)

    # front wheel
    wfx = cab_x + int(cw * 0.5)
    wfy = cab_y + ch - 1
    draw.ellipse([wfx - wheel_r, wfy - wheel_r, wfx + wheel_r, wfy + wheel_r], fill=FG)
    draw.ellipse([wfx - hub_r, wfy - hub_r, wfx + hub_r, wfy + hub_r], fill=BG)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for size in SIZES:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

        # Draw rounded-corner background
        draw = ImageDraw.Draw(img)
        corner_r = max(2, int(size * 0.2))
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=corner_r, fill=BG)

        # Draw the truck
        draw_truck(img)

        # Save as PNG
        out_path = os.path.join(OUTPUT_DIR, f"icon-{size}.png")
        img.save(out_path, "PNG")
        print(f"  {out_path}  ({size}x{size})")

    # Also produce favicon.ico with 16x16 and 32x32
    img16 = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    ImageDraw.Draw(img16).rounded_rectangle([0, 0, 15, 15], radius=3, fill=BG)
    draw_truck(img16)

    img32 = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    ImageDraw.Draw(img32).rounded_rectangle([0, 0, 31, 31], radius=6, fill=BG)
    draw_truck(img32)

    ico_path = os.path.join(OUTPUT_DIR, "favicon.ico")
    img16.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32)],
               append_images=[img32])
    print(f"  {ico_path}  (16+32 ICO)")

    print("Done — all icons generated.")


if __name__ == "__main__":
    main()
