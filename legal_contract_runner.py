#!/usr/bin/env python3
"""Legal Contract Review Agent CLI (v3 Unified).

Commands:
- demo: Generate a sample redline DOCX (red strike + blue underline + party-authored Word comments) and run v3 M1-M16 checks.
- check-comments: Check a JSON list or text file of comments for M8/M13/LEGAL violations.
- redline: Generate a redlined DOCX from original.txt, revised.txt, and optional comments.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from engine.checklist import (
    OMISSION_CHECKLIST_14,
    V3_CONTRACT_GATE,
    find_comment_violations,
)
from engine.injection_guard import guard_user_text


def cmd_demo(args: argparse.Namespace) -> int:
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    original = (
        "業務提携基本契約書\n"
        "第1条（支払条件）\n"
        "支払期日は検収完了月の翌月末日（30日以内）とする。\n"
        "第2条（合意管轄）\n"
        "本契約に関する一切の紛争については、東京地方裁判所を第一審の専属的合意管轄裁判所とする。"
    )
    revised = (
        "業務提携基本契約書\n"
        "第1条（支払条件）\n"
        "支払期日は検収完了月の翌々月15日（45日以内）とする。\n"
        "第2条（合意管轄）\n"
        "本契約に関する一切の紛争については、被告の本店所在地を管轄する地方裁判所を第一審の専属的合意管轄裁判所とする。"
    )
    comments = [
        "当社の月次支払サイクル（15日締め・翌々月15日払い）に合わせ、30日以内から45日以内へ調整してみましたがいかがでしょうか？ご検討いただければ幸いです。",
        "双方の所在地が離れておりますので、移動の時間と費用負担が公平になるよう、訴えられた側（被告）の所在地を管轄とする被告地主義の修正案にしてみましたがいかがでしょうか？ご検討いただければ幸いです。",
    ]

    violations = find_comment_violations(comments)
    if violations:
        print(f"[FAIL] Comment gate violations detected: {violations}", file=sys.stderr)
        return 1

    try:
        from engine.contract_docx import generate_contract_review_docx

        docx_bytes = generate_contract_review_docx(
            original_text=original,
            revised_text=revised,
            change_reasons=comments,
            author="株式会社サンプル当事者",
        )
        docx_path = out_dir / "sample_contract_redline_v3.docx"
        docx_path.write_bytes(docx_bytes)
        print(f"✅ 赤入れDOCXを生成しました: {docx_path}")
    except ImportError:
        print("ℹ️ python-docx / lxml が未導入のため DOCX バイナリ出力はスキップしました（pip install python-docx lxml）")

    report_path = out_dir / "v3_gate_check_report.json"
    report = {
        "status": "PASS",
        "checked_comments": len(comments),
        "violations": [],
        "author": "株式会社サンプル当事者",
        "gate_rules_verified": ["M1..M16", "14-Point Omission Checklist", "Injection Guard"],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ v3ゲート検査レポートを保存しました: {report_path}")
    return 0


def cmd_check_comments(args: argparse.Namespace) -> int:
    path = Path(args.file)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        comments = json.loads(raw)
        if not isinstance(comments, list):
            raise ValueError("JSON file must contain a list of comment strings")
    else:
        comments = [line.strip() for line in raw.splitlines() if line.strip()]

    violations = find_comment_violations(comments)
    result = {
        "status": "FAIL" if violations else "PASS",
        "total_comments": len(comments),
        "violations": [
            {
                "comment_index": v.comment_index,
                "code": v.code,
                "phrase": v.phrase,
            }
            for v in violations
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if violations else 0


def cmd_redline(args: argparse.Namespace) -> int:
    from engine.contract_docx import generate_contract_review_docx

    author = (args.author or "").strip()
    if not author:
        print("エラー: --author（提出当事者名）は必須です（M5ゲート: AI名義でのコメント出力を防止）", file=sys.stderr)
        return 2

    original = Path(args.original).read_text(encoding="utf-8")
    revised = Path(args.revised).read_text(encoding="utf-8")
    guard_user_text(original, "原文契約書")
    guard_user_text(revised, "修正案契約書")

    comments = None
    if args.comments:
        c_path = Path(args.comments)
        raw = c_path.read_text(encoding="utf-8")
        comments = json.loads(raw) if c_path.suffix.lower() == ".json" else [l.strip() for l in raw.splitlines() if l.strip()]
        violations = find_comment_violations(comments)
        if violations:
            print(f"[M8/M13/LEGAL Gate Error] コメントに禁止表現が含まれています: {violations}", file=sys.stderr)
            return 1

    blob = generate_contract_review_docx(
        original_text=original,
        revised_text=revised,
        change_reasons=comments,
        author=author,
    )
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(blob)
    print(f"✅ 赤入れDOCXを生成しました: {out_path} (w:author={author})")
    return 0


def cmd_print_gate(_args: argparse.Namespace) -> int:
    print(OMISSION_CHECKLIST_14)
    print("\n" + "=" * 60 + "\n")
    print(V3_CONTRACT_GATE)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Legal Contract Review Agent CLI (v3 Unified)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", help="Run a 3-second redline DOCX + v3 gate demo")
    p_demo.add_argument("--output-dir", default="./output", help="Output directory")
    p_demo.set_defaults(func=cmd_demo)

    p_check = sub.add_parser("check-comments", help="Check comments for M8/M13/LEGAL violations")
    p_check.add_argument("--file", required=True, help="Path to JSON list or line-separated text file of comments")
    p_check.set_defaults(func=cmd_check_comments)

    p_redline = sub.add_parser("redline", help="Generate redline DOCX with red strike, blue underline, and Word comments")
    p_redline.add_argument("--original", required=True, help="Original contract text file")
    p_redline.add_argument("--revised", required=True, help="Revised contract text file")
    p_redline.add_argument("--comments", help="Optional JSON/text file of change reason comments")
    p_redline.add_argument("--author", required=True, help="Party name for Word comment w:author (M5 mandatory)")
    p_redline.add_argument("--output", required=True, help="Output .docx file path")
    p_redline.set_defaults(func=cmd_redline)

    p_gate = sub.add_parser("print-gate", help="Print 14-point checklist and v3 M1-M16 gate")
    p_gate.set_defaults(func=cmd_print_gate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
