"""Korean-input QA: file encoding, Unicode form, invisible / broken characters, and language checks.

Run before any model call, next to validator.validate_inputs, and reported with the same Issue levels:
- error   blocks the run: the text the model would see is not what the author wrote
          (undecodable file, broken U+FFFD, control characters, non-NFC text in a field sent to the model).
- warning is listed for a human to decide: may be intentional in an adversarial item (zero-width / bidi
          characters), or is a likely authoring slip (untranslated source text, no Hangul, mojibake).
Nothing here rewrites data. scripts/normalize_inputs.py writes a fixed copy (UTF-8, no BOM, NFC) to a new
directory for the encoding and NFC errors; everything else is a human decision.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .schemas import AI, ITEM_TAGS, ITEMS, PROMPTS, Table
from .validator import Issue, _key

# Characters that render as nothing (or as a plain space) but change what the model receives.
_INVISIBLE = {
    0x00A0: "NO-BREAK SPACE", 0x00AD: "SOFT HYPHEN", 0x115F: "HANGUL CHOSEONG FILLER",
    0x1160: "HANGUL JUNGSEONG FILLER", 0x3164: "HANGUL FILLER", 0xFFA0: "HALFWIDTH HANGUL FILLER",
}
_ALLOWED_CONTROL = {"\t", "\n", "\r"}
_HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")  # syllables + compatibility jamo (ㅋㅋ, ㅠㅠ)
_LATIN = re.compile(r"[A-Za-z]")
_MOJIBAKE_HINT = re.compile(r"[À-ÿ]")
_TURN_TEXT_FIELDS = ("message_text", "context_text")

MAX_MESSAGE_CHARS = 4000
MIN_HANGUL_SHARE = 0.3  # Hangul / (Hangul + Latin letters); youth chat mixes in English words


class InputEncodingError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------- files

def check_file_encoding(path: str | Path) -> list[Issue]:
    """Raise InputEncodingError if the file is not UTF-8; warn on a UTF-8 BOM (read_table strips it)."""
    path = Path(path)
    data = path.read_bytes()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        guess = ""
        for enc in ("cp949", "utf-16"):
            try:
                data.decode(enc)
                guess = f" ({enc.upper()}로 저장된 것으로 보임)"
                break
            except UnicodeDecodeError:
                continue
        raise InputEncodingError(
            f"{path.name}: UTF-8이 아님{guess}, {e.start}바이트 위치. "
            f"scripts/normalize_inputs.py로 UTF-8 사본을 만들거나 엑셀에서 'CSV UTF-8'로 다시 저장") from None
    if data.startswith(b"\xef\xbb\xbf"):
        return [Issue("warning", path.stem, path.name, "(file)",
                      "UTF-8 BOM 있음 (엑셀 'CSV UTF-8' 저장) — 읽을 때 제거함")]
    return []


# ---------------------------------------------------------------------------------------------- cells

def _char_name(ch: str) -> str:
    return f"U+{ord(ch):04X} {_INVISIBLE.get(ord(ch)) or unicodedata.name(ch, '?')}"


def _is_emoji_zwj(text: str, i: int) -> bool:
    """U+200D between two pictographs is part of an emoji sequence (👨‍👩‍👧), not obfuscation."""
    def pict(j):
        return 0 <= j < len(text) and (ord(text[j]) >= 0x1F000 or unicodedata.category(text[j]) == "So"
                                       or text[j] == "️")
    return pict(i - 1) and pict(i + 1)


def _looks_mojibake(v: str) -> bool:
    """UTF-8 Korean decoded as Latin-1/CP1252 ('ì•ˆë…•'): re-encoding recovers Hangul."""
    if not _MOJIBAKE_HINT.search(v):
        return False
    for enc in ("cp1252", "latin-1"):
        try:
            fixed = v.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if _HANGUL.search(fixed):
            return True
    return False


def check_cell(table: Table, row: dict, field: str, value: str) -> list[Issue]:
    if not value:
        return []
    sent = table.field(field).ai != AI.NEVER  # reaches the target model (message_text, persona_text, …)
    key, out = _key(table, row), []

    def add(level, msg):
        out.append(Issue(level, table.name, key, field, msg))

    if "�" in value:
        add("error", "깨진 문자 U+FFFD(�) — 인코딩 변환 중 손상된 텍스트")
    ctrl = sorted({c for c in value if unicodedata.category(c) == "Cc" and c not in _ALLOWED_CONTROL})
    if ctrl:
        add("error", "제어 문자 " + ", ".join(f"U+{ord(c):04X}" for c in ctrl))
    if not unicodedata.is_normalized("NFC", value):
        jamo = any(0x1100 <= ord(c) <= 0x11FF for c in value)
        add("error" if sent else "warning",
            "유니코드 NFC 아님" + (" (한글이 자모로 분리됨, macOS 등에서 생긴 NFD)" if jamo else "")
            + " — scripts/normalize_inputs.py로 NFC 사본 생성")
    invisible = sorted({c for i, c in enumerate(value)
                        if (ord(c) in _INVISIBLE or unicodedata.category(c) == "Cf")
                        and not (c == "‍" and _is_emoji_zwj(value, i))})
    if invisible:
        add("warning", "보이지 않는 문자 " + ", ".join(_char_name(c) for c in invisible)
            + " — 의도된 우회 표현이 아니면 제거")
    if _looks_mojibake(value):
        add("warning", "글자 깨짐(mojibake) 의심 — UTF-8 한글을 Latin-1/CP1252로 읽은 형태")
    if field in _TURN_TEXT_FIELDS:
        if value != value.strip():
            add("warning", "앞뒤 공백·줄바꿈 있음 (모델에 그대로 전달됨)")
        if len(value) > MAX_MESSAGE_CHARS:
            add("warning", f"길이 {len(value)}자 > {MAX_MESSAGE_CHARS}자")
    return out


def check_text_cells(items: list[dict], prompts: list[dict], item_tags: list[dict] | None) -> list[Issue]:
    issues: list[Issue] = []
    for table, rows in ((ITEMS, items), (PROMPTS, prompts), (ITEM_TAGS, item_tags or [])):
        for row in rows:
            for f in table.fields:
                issues += check_cell(table, row, f.name, row.get(f.name, ""))
    return issues


# ------------------------------------------------------------------------------------------- language

def hangul_share(text: str) -> float | None:
    h, latin = len(_HANGUL.findall(text)), len(_LATIN.findall(text))
    return None if h + latin == 0 else h / (h + latin)


def check_language(items: list[dict], prompts: list[dict], expected_language: str) -> list[Issue]:
    """Korean benchmark checks; only run when the config/manifest says the turns should be Korean."""
    if not expected_language.lower().startswith("ko"):
        return []
    issues: list[Issue] = []
    by_item: dict[tuple, list[dict]] = {}
    for p in prompts:
        by_item.setdefault((p["item_id"], p["item_version"]), []).append(p)
        text = p.get("message_text", "")
        share = hangul_share(text)
        if share == 0:
            issues.append(Issue("warning", "prompts", p["turn_id"], "message_text", "한글 없음 — 번역 누락 확인"))
        elif share is not None and share < MIN_HANGUL_SHARE:
            issues.append(Issue("warning", "prompts", p["turn_id"], "message_text",
                                f"한글 비중 {share:.0%} (한글/(한글+영문) < {MIN_HANGUL_SHARE:.0%})"))
    for it in items:
        if it.get("source_language", "").lower().startswith("ko") or it.get("localization_type") == "new":
            continue
        original = " ".join(it.get("original_text", "").split())
        if not original:
            continue
        for p in by_item.get((it["item_id"], it["item_version"]), []):
            msg = " ".join(p.get("message_text", "").split())
            if len(msg) >= 20 and msg in original:
                issues.append(Issue("warning", "prompts", p["turn_id"], "message_text",
                                    f"원문(original_text, {it.get('source_language')})과 같은 문장 — 번역되지 않음"))
    return issues


def check_korean_inputs(root: str | Path | None, items: list[dict], prompts: list[dict],
                        item_tags: list[dict] | None, expected_language: str | None) -> list[Issue]:
    """All text QA for a dataset. root=None skips the file-encoding check (in-memory data)."""
    issues: list[Issue] = []
    if root is not None:
        for name in ("items.csv", "prompts.csv", "item_tags.csv"):
            if (Path(root) / name).exists():
                issues += check_file_encoding(Path(root) / name)
    issues += check_text_cells(items, prompts, item_tags)
    if expected_language:
        issues += check_language(items, prompts, expected_language)
    return issues
