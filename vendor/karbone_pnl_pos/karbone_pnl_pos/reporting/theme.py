from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    ink: str = '#0b1220'
    muted: str = '#5b6b7e'
    subtle: str = '#8a97a8'
    divider: str = '#e6eaef'
    bg: str = '#f1f3f5'
    card: str = '#ffffff'
    accent: str = '#0a2540'
    pos: str = '#15803d'
    neg: str = '#b91c1c'
    pos_bg: str = '#ecfdf5'
    neg_bg: str = '#fef2f2'
    wash: str = '#f7f8fa'
    total_bg: str = '#eef2f6'
    total_rule: str = '#0a2540'


NUM_FONT = "'IBM Plex Mono', Consolas, ui-monospace, 'SF Mono', Menlo, monospace"
SANS_FONT = "'IBM Plex Sans', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"

THEME = Theme()
