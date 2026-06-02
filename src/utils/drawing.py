import cv2
import numpy as np
from PIL import ImageFont, ImageDraw, Image


def draw_korean_text(frame, text, pos, font_size=28, color=(255, 255, 255)):
    try:
        font_path = "C:/Windows/Fonts/malgun.ttf"
        font = ImageFont.truetype(font_path, font_size)

        img_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)

        rgb_color = (color[2], color[1], color[0])
        draw.text(pos, text, font=font, fill=rgb_color)

        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

    except:
        cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        return frame


def draw_polygon_roi(frame, polygon, color, label):
    overlay = frame.copy()
    cv2.fillPoly(overlay, [polygon], color)
    frame = cv2.addWeighted(overlay, 0.25, frame, 0.75, 0)
    cv2.polylines(frame, [polygon], True, color, 3)

    x, y, w, h = cv2.boundingRect(polygon)

    cv2.putText(
        frame,
        label,
        (x, max(30, y - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        color,
        2,
    )

    return frame