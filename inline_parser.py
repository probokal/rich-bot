"""
inline_parser.py — превращает обычный текст пользователя в список
сегментов с форматированием. Поддерживаемые маркеры:
  *жирный*  _курсив_  `код`  ~~зачёркнутый~~  ==выделенный==  ||спойлер||
"""
import re
from typing import List, Tuple


def parse_inline(text: str) -> List[Tuple[str, str]]:
    """
    Возвращает список пар (формат, текст).
    Формат ∈ {normal,bold,italic,code,strike,marked,spoiler}
    """
    # Порядок важен: сначала более «длинные» маркеры.
    pattern = re.compile(
        r"(?P<bold>\*\*(.+?)\*\*)"
        r"|(?P<strike>~~(.+?)~~)"
        r"|(?P<marked>==(.+?)==)"
        r"|(?P<spoiler>\|\|(.+?)\|\|)"
        r"|(?P<italic>_(.+?)_)"
        r"|(?P<code>`(.+?)`)"
    )
    result: List[Tuple[str, str]] = []
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            result.append(("normal", text[last : m.start()]))
        kind = m.lastgroup
        value = m.group(m.lastindex + 1) if m.lastindex else m.group()
        fmt = {
            "bold": "bold", "strike": "strike", "marked": "marked",
            "spoiler": "spoiler", "italic": "italic", "code": "code",
        }.get(kind, "normal")
        result.append((fmt, value))
        last = m.end()
    if last < len(text):
        result.append(("normal", text[last:]))
    return result
