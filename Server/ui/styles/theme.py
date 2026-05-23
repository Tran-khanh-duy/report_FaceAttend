"""
ui/styles/theme.py
Premium Light-Mode Clean Enterprise UI — Modern SaaS Dashboard, Low Cognitive Load.
"""
from PyQt6.QtGui import QColor, QPalette

class Colors:
    # ── Nền ──────────────────────────────────
    BG_APP      = "#F8FAFC"   # Very Light Slate Gray (App Background)
    BG_CARD     = "#FFFFFF"   # Pure White (Card/Surface)
    BG_INPUT    = "#F1F5F9"   # Light Gray for inputs
    BG_HOVER    = "#F1F5F9"   # Row hover
    BG_SELECTED = "#E0E7FF"   # Selected item

    # ── Viền ─────────────────────────────────
    BORDER      = "#E2E8F0"   # Light Gray (Border/Dividers)
    BORDER_LT   = "#CBD5E1"

    # ── Accent ───────────────────────────────
    PRIMARY     = "#2563EB"   # Royal Blue
    SUCCESS     = "#10B981"   # Emerald Green (Success/Online)
    WARNING     = "#F59E0B"   # Amber (Warning)
    DANGER      = "#EF4444"   # Red (Danger/Offline)

    # ── Văn bản ───────────────────────────────
    TEXT_PRI    = "#0F172A"   # Dark Navy/Black
    TEXT_SEC    = "#64748B"   # Medium Gray

    # Alias để tương thích ngược nếu ứng dụng có gọi các tên biến cũ
    CYAN = PRIMARY
    GREEN = SUCCESS
    RED = DANGER
    TEXT = TEXT_PRI
    TEXT_DIM = TEXT_SEC
    TEXT_DARK = TEXT_SEC
    
    BG_DARK = BG_APP
    BG_PANEL = BG_CARD
    CYAN_DIM = PRIMARY
    GREEN_DIM = SUCCESS
    RED_DIM = DANGER
    RED_LT = DANGER
    ORANGE = WARNING
    PURPLE = PRIMARY

    # ── Camera ───────────────────────────────
    CAM_BG      = "#E2E8F0"   # Light Gray for camera placeholder
    CAM_ON      = SUCCESS
    CAM_OFF     = DANGER
    CAM_WAIT    = WARNING


MAIN_STYLESHEET = f"""
/* ── Global ── */
QWidget {{
    background-color: {Colors.BG_APP};
    color: {Colors.TEXT_PRI};
    font-family: "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
    font-size: 14px;
}}
QMainWindow {{
    background-color: {Colors.BG_APP};
}}

/* ── Card/Panel (QFrame) ── */
QFrame {{
    background-color: {Colors.BG_CARD};
    border-radius: 12px;
    border: none;
}}

/* ── Scrollbar ── */
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    border-radius: 3px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {Colors.BORDER_LT};
    border-radius: 3px;
    min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{ background: {Colors.PRIMARY}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {Colors.BORDER_LT};
    border-radius: 3px;
}}
QScrollBar::handle:horizontal:hover {{ background: {Colors.PRIMARY}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ── Button ── */
QPushButton {{
    background-color: {Colors.BG_CARD};
    color: {Colors.TEXT_PRI};
    border: 1px solid {Colors.BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
    font-size: 14px;
    min-height: 36px;
}}
QPushButton:hover {{
    background-color: {Colors.BG_HOVER};
    border-color: {Colors.PRIMARY};
    color: {Colors.PRIMARY};
}}
QPushButton:pressed {{
    background-color: {Colors.BG_SELECTED};
}}

/* ── Input ── */
QLineEdit, QComboBox, QSpinBox, QDateEdit, QTimeEdit {{
    background-color: {Colors.BG_CARD};
    color: {Colors.TEXT_PRI};
    border: 1px solid {Colors.BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 14px;
    min-height: 36px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {{
    border-color: {Colors.PRIMARY};
}}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {Colors.TEXT_SEC};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {Colors.BG_CARD};
    border: 1px solid {Colors.BORDER};
    border-radius: 8px;
    selection-background-color: {Colors.BG_HOVER};
    selection-color: {Colors.PRIMARY};
}}

/* ── Label & ToolButton ── */
QLabel, QToolButton {{
    background: transparent;
    color: {Colors.TEXT_PRI};
    border: none;
}}

/* ── Container No Borders (QGroupBox, QScrollArea, Lists) ── */
QGroupBox {{
    border: 1px solid transparent;
    background: transparent;
    margin-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 0px;
    padding: 0px;
    color: {Colors.PRIMARY};
    font-weight: bold;
    background: transparent;
}}
QScrollArea, QListView, QListWidget {{
    border: 1px solid transparent;
    background: transparent;
}}

/* ── Table ── */
QTableWidget {{
    background-color: {Colors.BG_CARD};
    border: none;
    gridline-color: transparent;
    color: {Colors.TEXT_PRI};
    font-size: 14px;
}}
QTableWidget::item {{
    padding: 10px 14px;
    border-bottom: 1px solid {Colors.BORDER};
}}
QTableWidget::item:hover {{
    background-color: {Colors.BG_HOVER};
}}
QTableWidget::item:selected {{
    background-color: {Colors.BG_HOVER};
    color: {Colors.PRIMARY};
}}
QHeaderView::section {{
    background-color: transparent;
    color: {Colors.TEXT_SEC};
    font-weight: bold;
    font-size: 13px;
    padding: 12px 14px;
    border: none;
    border-bottom: 1px solid {Colors.BORDER};
}}

/* ── ProgressBar ── */
QProgressBar {{
    background-color: {Colors.BG_INPUT};
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {Colors.PRIMARY};
    border-radius: 6px;
}}
"""

def apply_theme(app):
    """Áp dụng StyleSheet và Palette tổng thể cho ứng dụng."""
    app.setStyleSheet(MAIN_STYLESHEET)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(Colors.BG_APP))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(Colors.TEXT_PRI))
    palette.setColor(QPalette.ColorRole.Base,            QColor(Colors.BG_CARD))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(Colors.BG_CARD))
    palette.setColor(QPalette.ColorRole.Text,            QColor(Colors.TEXT_PRI))
    palette.setColor(QPalette.ColorRole.Button,          QColor(Colors.BG_CARD))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(Colors.TEXT_PRI))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(Colors.PRIMARY))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(Colors.BG_CARD))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(Colors.TEXT_SEC))
    app.setPalette(palette)

def create_shadow():
    """Hàm Helper: Tạo đổ bóng siêu mềm mại (Soft Drop Shadows) cho các Card"""
    from PyQt6.QtWidgets import QGraphicsDropShadowEffect
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(15)
    shadow.setColor(QColor(0, 0, 0, 25)) # 10% opacity của Đen (25/255)
    shadow.setOffset(0, 4)
    return shadow

def card_style(accent: str = None, radius: int = 12) -> str:
    """Style CSS cho các panel dạng thẻ (Card)"""
    c = accent or Colors.BORDER
    return (
        f"background-color: {Colors.BG_CARD};"
        f"border: 1px solid {c};"
        f"border-radius: {radius}px;"
    )

def badge_style(color: str) -> str:
    """Style CSS cho các nhãn trạng thái (Pill shape badge)"""
    return (
        f"background-color: {color}1A;"
        f"color: {color};"
        f"border: 1px solid {color}44;"
        f"border-radius: 12px;"
        f"padding: 4px 12px;"
        f"font-size: 12px;"
        f"font-weight: 700;"
    )

def combo_style() -> str:
    """Style đặc chế cho QComboBox nếu cần ghi đè"""
    return f"""
        QComboBox {{
            background: {Colors.BG_CARD};
            color: {Colors.TEXT_PRI};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 13px;
            min-height: 36px;
        }}
        QComboBox:focus {{ border-color: {Colors.PRIMARY}; }}
        QComboBox:disabled {{
            background: {Colors.BG_APP};
            color: {Colors.TEXT_SEC};
        }}
        QComboBox::drop-down {{ border: none; width: 30px; }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid {Colors.TEXT_SEC};
            margin-right: 10px;
        }}
        QComboBox QAbstractItemView {{
            background: {Colors.BG_CARD};
            color: {Colors.TEXT_PRI};
            border: 1px solid {Colors.BORDER_LT};
            border-radius: 8px;
            selection-background-color: {Colors.BG_HOVER};
            selection-color: {Colors.PRIMARY};
            outline: none;
        }}
    """

def input_style() -> str:
    """Style đặc chế cho QLineEdit nếu cần ghi đè"""
    return f"""
        QLineEdit {{
            background: {Colors.BG_CARD};
            color: {Colors.TEXT_PRI};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 13px;
            min-height: 36px;
        }}
        QLineEdit:focus {{ border-color: {Colors.PRIMARY}; }}
        QLineEdit:disabled {{
            background: {Colors.BG_APP};
            color: {Colors.TEXT_SEC};
        }}
    """