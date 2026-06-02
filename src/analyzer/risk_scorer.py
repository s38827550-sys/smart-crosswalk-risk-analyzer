import cv2
import numpy as np
from PIL import ImageFont, ImageDraw, Image


class RiskMonitorWindow:
    def __init__(self):
        self.window_name = "Risk Reason Monitor"
        self.risk_score = 0

        # 추가: 증가/감소 요인 리스트
        self.increase_reasons = []
        self.decrease_reasons = []

        # 상세 상태 표시용
        self.detail = ""

    # 수정: status, reason 제거
    # 증가요인 리스트 / 감소요인 리스트를 직접 받음
    def update(self, risk_score, increase_reasons=None, decrease_reasons=None, detail=""):
        self.risk_score = risk_score
        self.increase_reasons = increase_reasons if increase_reasons else []
        self.decrease_reasons = decrease_reasons if decrease_reasons else []
        self.detail = detail

    def draw_korean_text(self, frame, text, pos, font_size=26, color=(255, 255, 255)):
        try:
            font_path = "C:/Windows/Fonts/malgun.ttf"
            font = ImageFont.truetype(font_path, font_size)

            img_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(img_pil)

            rgb_color = (color[2], color[1], color[0])
            draw.text(pos, text, font=font, fill=rgb_color)

            return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

        except:
            cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            return frame

    def draw_status_light(self, frame, text, pos, is_on, color_on):
        x, y = pos

        light_color = color_on if is_on else (90, 90, 90)

        cv2.circle(frame, (x, y + 14), 10, light_color, -1)

        frame = self.draw_korean_text(
            frame,
            text,
            (x + 25, y),
            24,
            (255, 255, 255),
        )

        return frame

    def wrap_text(self, text, max_len=28):
        lines = []
        current = ""

        for word in text.split(" "):
            if len(current) + len(word) + 1 > max_len:
                lines.append(current)
                current = word
            else:
                if current:
                    current += " "
                current += word

        if current:
            lines.append(current)

        return lines

    def draw_reason_list(self, panel, title, reasons, start_y, title_color):
        panel = self.draw_korean_text(
            panel,
            title,
            (30, start_y),
            24,
            title_color,
        )

        y = start_y + 38

        if not reasons:
            panel = self.draw_korean_text(
                panel,
                "- 없음",
                (50, y),
                20,
                (160, 160, 160),
            )
            return panel, y + 32

        for idx, reason in enumerate(reasons[:5], start=1):
            line_text = f"{idx}. {reason}"

            for line in self.wrap_text(line_text, max_len=34):
                panel = self.draw_korean_text(
                    panel,
                    line,
                    (50, y),
                    20,
                    (255, 255, 255),
                )
                y += 28

        return panel, y + 12

    def show(self):
        panel = np.zeros((720, 820, 3), dtype=np.uint8)
        panel[:] = (30, 30, 30)

        panel = self.draw_korean_text(
            panel,
            "실시간 위험도 분석 로그",
            (30, 30),
            32,
            (255, 255, 255),
        )

        panel = self.draw_korean_text(
            panel,
            f"현재 위험도: {int(self.risk_score)} / 100",
            (30, 95),
            30,
            (0, 0, 255) if self.risk_score >= 60 else (0, 255, 255),
        )

        # ✅ 상태를 증가/감소로 분리해서 불 표시
        increase_on = len(self.increase_reasons) > 0
        decrease_on = len(self.decrease_reasons) > 0

        panel = self.draw_status_light(
            panel,
            "증가 요인 감지",
            (35, 155),
            increase_on,
            (0, 0, 255),
        )

        panel = self.draw_status_light(
            panel,
            "감소 요인 감지",
            (35, 200),
            decrease_on,
            (0, 255, 255),
        )

        # 핵심 판단 제거
        # 증가 요인 / 감소 요인 따로 표시
        panel, next_y = self.draw_reason_list(
            panel,
            "위험도 증가 요인:",
            self.increase_reasons,
            265,
            (0, 0, 255),
        )

        panel, next_y = self.draw_reason_list(
            panel,
            "위험도 감소 요인:",
            self.decrease_reasons,
            next_y + 10,
            (0, 255, 255),
        )

        if self.detail:
            panel = self.draw_korean_text(
                panel,
                "상세 상태:",
                (30, next_y + 10),
                22,
                (200, 200, 200),
            )

            y = next_y + 45
            for line in self.wrap_text(self.detail, max_len=40):
                panel = self.draw_korean_text(
                    panel,
                    line,
                    (50, y),
                    18,
                    (200, 200, 200),
                )
                y += 24

        cv2.imshow(self.window_name, panel)