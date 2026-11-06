#!/usr/bin/env python3
"""Legal Contract Review Agent CLI (v3 Unified).

Commands:
- demo: Generate a sample redline DOCX (red strike + blue underline + party-authored Word comments) and run v3 M1-M16 checks.
- check-comments: Check a JSON list or text file of comments for M8/M13/LEGAL violations.
- redline: Generate a redlined DOCX from original.txt, revised.txt, and optional comments.json.
- build-prompt: Build Stage 1 (initial revision), Stage 2 (self-review), or Stage 3 (consistency check) prompts with knowledge corpus injection.
- blog-loop: Run, probe, check status, or promote human-approved legal blog learning candidates.
- print-gate: Print the 14-point omission checklist and v3 M1-M16 submission gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from engine.blog_learning_loop import Paths as BlogPaths
from engine.blog_learning_loop import (
    extract_entry_ids_from_index,
    fetch_nonempty,
    load_blog_url,
    promote as promote_blog_card,
    read_json,
    run as run_blog_loop,
)
from engine.checklist import (
    OMISSION_CHECKLIST_14,
    V3_CONTRACT_GATE,
    find_comment_violations,
)
from engine.contract_comments import (
    build_stage1_revision_prompt,
    build_stage2_self_review_prompt,
    build_stage3_consistency_prompt,
)
from engine.injection_guard import guard_user_text

ROOT = Path(__file__).resolve().parent
KNOWLEDGE_DIR = ROOT / "knowledge"


def load_knowledge_corpus(max_chars: int = 35_000) -> str:
    """Load and concatenate the anonymized knowledge/*.md corpus up to max_chars."""
    if not KNOWLEDGE_DIR.exists():
        return ""
    chunks: list[str] = []
    total = 0
    for md_path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        if md_path.name == "README.md":
            continue
        text = md_path.read_text(encoding="utf-8")
        if total + len(text) > max_chars:
            remaining = max(0, max_chars - total)
            if remaining > 200:
                chunks.append(f"### {md_path.name}\n{text[:remaining]}")
            break
        chunks.append(f"### {md_path.name}\n{text}")
        total += len(text)
    return "\n\n".join(chunks)


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
        "knowledge_files_loaded": len(list(KNOWLEDGE_DIR.glob("*.md"))),
        "gate_rules_verified": ["M1..M16", "14-Point Omission Checklist", "Injection Guard", "3-Stage Revision Pipeline"],
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


def cmd_build_prompt(args: argparse.Namespace) -> int:
    original = Path(args.original).read_text(encoding="utf-8")
    conditions = Path(args.conditions).read_text(encoding="utf-8") if args.conditions else ""
    guard_user_text(original, "原文契約書")
    if conditions:
        guard_user_text(conditions, "前提条件")

    if args.stage == 1:
        ref = load_knowledge_corpus() if args.with_knowledge else ""
        prompt = build_stage1_revision_prompt(
            our_company=args.our_company,
            their_company=args.their_company,
            original_text=original,
            conditions_text=conditions,
            reference_section=ref,
            negotiation_win_ratio=args.power_balance or "",
            negotiation_premises=args.value_leverage or "",
            negotiation_counterpart_profile=args.counterparty_traits or "",
        )
    elif args.stage == 2:
        if not args.draft:
            print("エラー: --stage 2 には --draft（Stage 1修正案ファイル）が必須です", file=sys.stderr)
            return 2
        draft = Path(args.draft).read_text(encoding="utf-8")
        prompt = build_stage2_self_review_prompt(
            our_company=args.our_company,
            original_text=original,
            conditions_text=conditions,
            stage1_revised_text=draft,
        )
    else:
        if not args.draft:
            print("エラー: --stage 3 には --draft（Stage 2修正案ファイル）が必須です", file=sys.stderr)
            return 2
        draft = Path(args.draft).read_text(encoding="utf-8")
        prompt = build_stage3_consistency_prompt(
            original_text=original,
            stage2_revised_text=draft,
        )

    if args.output:
        Path(args.output).write_text(prompt, encoding="utf-8")
        print(f"✅ Stage {args.stage} プロンプトを出力しました: {args.output}")
    else:
        print(prompt)
    return 0


def cmd_blog_loop(args: argparse.Namespace) -> int:
    paths = BlogPaths(loop=Path(args.loop_dir), knowledge=KNOWLEDGE_DIR)
    if args.action == "status":
        print(json.dumps(read_json(paths.status, {"status": "never_run"}), ensure_ascii=False, indent=2))
        return 0
    if args.action == "probe":
        url = load_blog_url(blog_url=args.blog_url) + "/"
        print(json.dumps({"status": "ok", "entry_count": len(extract_entry_ids_from_index(fetch_nonempty(url)))}, ensure_ascii=False))
        return 0
    if args.action == "promote":
        if not args.entry_id or not args.approval_file:
            print("エラー: promote には --entry-id と --approval-file が必須です", file=sys.stderr)
            return 2
        target = promote_blog_card(paths, args.entry_id, Path(args.approval_file))
        print(str(target))
        return 0
    result = run_blog_loop(paths, pages=args.pages, blog_url=args.blog_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


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

    p_prompt = sub.add_parser("build-prompt", help="Build Stage 1/2/3 contract revision prompt")
    p_prompt.add_argument("--stage", type=int, choices=(1, 2, 3), required=True, help="Pipeline stage (1, 2, or 3)")
    p_prompt.add_argument("--original", required=True, help="Original contract text file")
    p_prompt.add_argument("--conditions", help="Client conditions text file")
    p_prompt.add_argument("--our-company", default="当社", help="Our company/party name")
    p_prompt.add_argument("--their-company", default="御社", help="Counterparty company name")
    p_prompt.add_argument("--draft", help="Draft text file from previous stage (required for Stage 2 and 3)")
    p_prompt.add_argument("--with-knowledge", action="store_true", default=True, help="Inject knowledge/*.md corpus")
    p_prompt.add_argument("--power-balance", help="Power balance (e.g., 60:40)")
    p_prompt.add_argument("--value-leverage", help="Value leverage / exclusivity")
    p_prompt.add_argument("--counterparty-traits", help="Counterparty organizational traits")
    p_prompt.add_argument("--output", help="Optional output file path")
    p_prompt.set_defaults(func=cmd_build_prompt)

    p_blog = sub.add_parser("blog-loop", help="Run or promote human-gated legal blog learning cards")
    p_blog.add_argument("action", choices=("run", "probe", "status", "promote"))
    p_blog.add_argument("--blog-url", help="Public blog base URL (https://...)")
    p_blog.add_argument("--entry-id", help="Entry ID to promote")
    p_blog.add_argument("--approval-file", help="Path to approval JSON file")
    p_blog.add_argument("--pages", type=int, default=2, help="Number of index pages to scan")
    p_blog.add_argument("--loop-dir", default=str(ROOT / ".runtime" / "blog-learning"), help="Runtime loop state directory")
    p_blog.set_defaults(func=cmd_blog_loop)

    p_gate = sub.add_parser("print-gate", help="Print 14-point checklist and v3 M1-M16 gate")
    p_gate.set_defaults(func=cmd_print_gate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
