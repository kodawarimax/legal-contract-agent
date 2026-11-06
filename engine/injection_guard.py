"""Prompt injection detection and XML boundary sanitization for contract inputs."""
from __future__ import annotations

import html
import re
from typing import List, Tuple

_PATTERNS: List[Tuple[re.Pattern, float]] = [
    # English direct-override phrases
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|guidelines?)", re.I), 0.9),
    (re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", re.I), 0.9),
    (re.compile(r"forget\s+(all\s+)?(previous|prior|your)\s+(instructions?|training|rules?)", re.I), 0.85),
    (re.compile(r"you\s+are\s+now\s+(a|an)\s+\w+", re.I), 0.7),
    (re.compile(r"(act|behave)\s+as\s+(if\s+you\s+(are|were)|a|an)\s+", re.I), 0.6),
    (re.compile(r"new\s+(role|persona|identity|instructions?|system\s+prompt)", re.I), 0.65),
    (re.compile(r"override\s+(your\s+)?(previous\s+)?(instructions?|settings?|rules?|system)", re.I), 0.85),
    (re.compile(r"system\s*prompt", re.I), 0.6),
    (re.compile(r"jailbreak", re.I), 0.9),
    (re.compile(r"DAN\s+mode", re.I), 0.9),
    (re.compile(r"developer\s+mode", re.I), 0.7),
    (re.compile(r"(print|output|reveal|show|repeat)\s+(your\s+)?(system\s+prompt|instructions?|training\s+data)", re.I), 0.85),
    # Japanese direct-override phrases
    (re.compile(r"(前の|以前の|これまでの|すべての)(指示|命令|ルール|プロンプト)を(無視|忘れ|削除)", re.U), 0.9),
    (re.compile(r"あなたは(実は|本当は|今から)", re.U), 0.7),
    (re.compile(r"ロール(を変え|チェンジ|プレイ(?!の)|を切り替え)", re.U), 0.6),
    (re.compile(r"(新しい|別の)(役割|ペルソナ|キャラ|指示|システムプロンプト)", re.U), 0.6),
    (re.compile(r"(システムプロンプト|プロンプト)を(見せ|教え|出力|表示)", re.U), 0.85),
    (re.compile(r"(指示|命令|設定)を(上書き|変更|リセット)", re.U), 0.75),
    # Injection delimiter tricks
    (re.compile(r"<\s*/?system\s*>", re.I), 0.9),
    (re.compile(r"\[INST\]|\[/INST\]", re.I), 0.8),
    (re.compile(r"###\s*(instruction|system|human|assistant)", re.I), 0.7),
    # Tool / OS injection
    (re.compile(r"(?i)(tool_call|function_call|<tool>|<function>)"), 0.8),
    (re.compile(r"(?i)(sudo|rm\s+-|chmod|/etc/)"), 0.85),
]

_ALL_PATTERNS: List[Tuple[re.Pattern, float]] = list(_PATTERNS)


def check_injection(user_text: str) -> float:
    """Compute injection suspicion score in [0.0, 1.0]. >= 0.7 is considered injection."""
    accumulated = 0.0
    for pattern, weight in _ALL_PATTERNS:
        if pattern.search(user_text or ""):
            accumulated += weight
    return min(accumulated, 1.0)


def wrap_context(context: str, source: str = "retrieved") -> str:
    """Wrap retrieved context so the LLM knows it is untrusted external data."""
    safe_source = html.escape(source, quote=True)
    safe_context = html.escape(context)
    return f'<context source="{safe_source}" trusted="false">\n{safe_context}\n</context>'


def wrap_user_input(user_text: str) -> str:
    """Wrap user input so the LLM sees a clear boundary."""
    return f"<user_input>\n{html.escape(user_text)}\n</user_input>"


def guard_user_text(text: str, field_label: str = "入力") -> str:
    """Validate untrusted text against prompt injection and wrap in escaped XML boundary."""
    if not text:
        return ""
    if check_injection(text) >= 0.7:
        raise ValueError(f"{field_label}に不正な指示パターンが含まれています")
    return wrap_user_input(text)
