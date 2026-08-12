from __future__ import annotations

import re
import unittest
from pathlib import Path


STYLES_PATH = Path(__file__).resolve().parents[1] / "static" / "styles.css"
THEME_BLOCK_RE = re.compile(
    r':root\[data-theme-family="([^"]+)"\]\[data-theme-mode="([^"]+)"\][^{]*\{([^}]*)\}',
    re.DOTALL,
)
VARIABLE_RE = re.compile(r"--([\w-]+):\s*(#[0-9a-f]{6})", re.IGNORECASE)


def _rgb(value: str) -> tuple[float, float, float]:
    return tuple(float(int(value[index : index + 2], 16)) for index in (1, 3, 5))


def _mix(
    foreground: tuple[float, float, float],
    background: tuple[float, float, float],
    foreground_weight: float,
) -> tuple[float, float, float]:
    return tuple(
        foreground[index] * foreground_weight
        + background[index] * (1 - foreground_weight)
        for index in range(3)
    )


def _luminance(color: tuple[float, float, float]) -> float:
    channels = []
    for value in color:
        channel = value / 255
        channels.append(
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    first_luminance = _luminance(first)
    second_luminance = _luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


class ThemeContrastTests(unittest.TestCase):
    def test_assignment_badge_is_high_contrast_in_every_theme_and_selected_state(self) -> None:
        styles = STYLES_PATH.read_text()
        badge_block = re.search(
            r"\.limited-assignment-badge\s*\{([^}]*)\}",
            styles,
            re.DOTALL,
        )
        self.assertIsNotNone(badge_block)
        badge_rules = badge_block.group(1)
        foreground_variable = re.search(r"color:\s*var\(--([\w-]+)\)", badge_rules)
        background_mix = re.search(
            r"background:\s*color-mix\(in srgb,\s*var\(--([\w-]+)\)\s*([\d.]+)%,\s*var\(--([\w-]+)\)\)",
            badge_rules,
        )
        self.assertIsNotNone(foreground_variable)
        self.assertIsNotNone(background_mix)

        themes = {}
        for family, mode, block in THEME_BLOCK_RE.findall(styles):
            themes[(family, mode)] = dict(VARIABLE_RE.findall(block))
        self.assertEqual(len(themes), 10)

        foreground_key = foreground_variable.group(1)
        accent_key = background_mix.group(1)
        accent_weight = float(background_mix.group(2)) / 100
        background_key = background_mix.group(3)
        for theme, variables in themes.items():
            foreground = _rgb(variables[foreground_key])
            background = _mix(
                _rgb(variables[accent_key]),
                _rgb(variables[background_key]),
                accent_weight,
            )
            self.assertGreaterEqual(
                _contrast_ratio(foreground, background),
                7,
                f"Assignment badge contrast is too low for {theme[0]} {theme[1]}",
            )

        selected_rules = re.search(
            r"\.conversation-item\.active \.conversation-assignment-badge,\s*"
            r"\.conversation-item\.selected \.conversation-assignment-badge\s*\{([^}]*)\}",
            styles,
            re.DOTALL,
        )
        self.assertIsNotNone(selected_rules)
        self.assertNotRegex(selected_rules.group(1), r"(?:background|color):")
        self.assertIn("visibility: visible", selected_rules.group(1))


if __name__ == "__main__":
    unittest.main()
