"""
契約書の3段階修正（Stage1初期修正 → Stage2セルフレビュー → Stage3原文整合性チェック）および
相手方向け戦略的コメント（Bさんスタイル）生成＆v3違反時の安全フォールバックエンジン。
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from .checklist import (
    OMISSION_CHECKLIST_14,
    OMISSION_REVIEW_14,
    V3_CONTRACT_GATE,
    build_negotiation_sim_block,
    find_comment_violations,
)
from .contract_docx import _strip_commentary
from .injection_guard import check_injection, guard_user_text, wrap_context, wrap_user_input

logger = logging.getLogger(__name__)

LLMCallable = Callable[[str], Awaitable[str]]


def validate_request_options(
    our_company: str,
    their_company: str = "",
    output_format: str = "docx",
    external_upload_confirmed: bool = False,
) -> tuple[str, str, str]:
    """M5（当事者名必須）と外部送信ゲートをLLM処理前に確定する。"""
    our = (our_company or "").strip()
    if not our:
        raise ValueError("コメントauthorに使う正式な当事者名を入力してください")
    their = (their_company or "").strip() or "御社"
    if any("\n" in value or "\r" in value or len(value) > 200 for value in (our, their)):
        raise ValueError("当事者名が不正です")
    if max(check_injection(our), check_injection(their)) >= 0.7:
        raise ValueError("当事者名にプロンプト注入の疑いがあります")

    normalized_format = (output_format or "docx").strip().lower()
    if normalized_format not in {"docx", "gdocs"}:
        raise ValueError("formatはdocxまたはgdocsを指定してください")
    if normalized_format == "gdocs" and not external_upload_confirmed:
        raise PermissionError("Google Docsへの外部送信には明示確認が必要です")
    return our, their, normalized_format


def para_diff_for_comments(old_text: str, new_text: str) -> List[Tuple[str, str, str]]:
    """段落レベルの差分を返す: [(tag, old_para, new_para), ...]"""
    old_paras = old_text.splitlines()
    new_paras = new_text.splitlines()
    sm = difflib.SequenceMatcher(None, old_paras, new_paras)
    result: List[Tuple[str, str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                result.append(("equal", old_paras[i], old_paras[i]))
        elif tag == "delete":
            for i in range(i1, i2):
                result.append(("delete", old_paras[i], ""))
        elif tag == "insert":
            for j in range(j1, j2):
                result.append(("insert", "", new_paras[j]))
        elif tag == "replace":
            old_chunk = old_paras[i1:i2]
            new_chunk = new_paras[j1:j2]
            pairs = max(len(old_chunk), len(new_chunk))
            for k in range(pairs):
                o = old_chunk[k] if k < len(old_chunk) else ""
                n = new_chunk[k] if k < len(new_chunk) else ""
                if not o:
                    result.append(("insert", "", n))
                elif not n:
                    result.append(("delete", o, ""))
                else:
                    result.append(("replace", o, n))
    return result


def build_changes_summary(diffs: List[Tuple[str, str, str]]) -> List[dict]:
    """差分リストから変更箇所のみ抽出してサマリーを作る。"""
    changes = []
    for idx, (tag, old, new) in enumerate(diffs):
        if tag != "equal":
            changes.append(
                {
                    "diff_index": idx,
                    "type": tag,
                    "original": old[:500],
                    "revised": new[:500],
                }
            )
    return changes


COMMENT_GENERATION_PROMPT = """\
あなたは契約書の修正案を相手方企業に提出する担当者です。
修正した契約書の右端コメント欄に、修正理由を記載してください。

## 最重要: 「誰が誰に」を絶対に間違えない

- **書き手**: 修正案を提出する側の担当者（自社＝修正した側）
- **読み手**: 契約の相手方企業の担当者（御社＝原案を作成した側）
- これは自社担当者が相手方担当者に向けて書く「提案理由」です
- 法律家が書いている体にしてはいけません
- 社内向けの説明になってもいけません

## 呼称ルール（厳守）

- 「甲」「乙」は絶対に使わない
- 「甲様」「乙様」は論外
- 自社のことは「当社」「弊社」
- 相手方のことは「御社」
- 具体的な会社名がわかる場合はそれを使ってもよい

## コメントのスタイル（厳守）

1. **相手の立場・利益を長期的な目線で重視する**: 修正が御社にとってもメリットがあることを具体的に説明する。「御社にとっても〜」「長期的なお取引を考えますと〜」等
2. **双方の事情を率直に説明する**: 自社の事情だけでなく、相手方の事情も理解していることを示す。「御社の〜というご事情も承知しておりますが」等
3. **提案型で終わる**: 命令や要求ではなく「〜という修正案にしてみましたがいかがでしょうか？ご検討いただければ幸いです。」で締める
4. **法律用語を避け、ビジネスの言葉で**: 「不平等」「一方的に有利」等の法律家的な対立表現は絶対NG。代わりに状況を具体的に描写して相手に「なるほど、それは確かに」と思わせる
5. **簡潔さ**: 1コメント2〜3文。核心のみ
6. **契約書内で定義された用語を首尾一貫して使う**: 第1条等で「顧客」「本製品」等と定義されている用語は、コメント内でも同じ用語を使うこと
7. **法的リスクは候補として示す**: 価格拘束、PL保険等は事実を具体的に示し、個別案件への法令適用は要弁護士確認にする

## v3コメントゲート

- **M8 呼称**: 相手は「貴社/御社」、自分は「私/当社/弊社」。G社、N社、○氏等の内部コード名は禁止
- **M13 内部過程**: 当初案、即時解除案、交渉シミュレーション、裁定、社内検討等の経緯を相手向けコメントへ出さない
- 相手が見ていない案との比較（「〜案から修正」「〜ではなく〜に整理」「〜のまま維持」）は禁止
- 法的最終判断はしない。「法的に問題ない」「このまま締結できる」と断定せず、必要な論点は「要確認」「要弁護士確認」とする

## デフォルトコメント例（Bさんスタイル＝模範）

- 「当社の所在地が東京、御社の所在地が福岡ですので、移動の時間や費用を考えるとどちらに決めても不満が残ります。よって訴えられた当事者（＝被告）の住所/所在地を管轄する裁判所で紛争解決をするという、両者平等な被告地主義の考えに基づいた修正案にしてみましたがいかがでしょうか？ご検討いただければ幸いです。」
- 「当社としましても御社の本製品を長期的に販売していきたいと考えております。独占販売権をいただける場合、他社競合品の取扱いを控えることには異存ございません。ただ、本契約時点ですでに当社が取り扱っている製品については引き続き販売させていただければ幸いです。」
- 「受入検査につきまして、当社の検査体制を考慮しますと7営業日必要となります。御社にとりましても、品質が確認された製品のみが市場に出ることは長期的なブランド保護につながるかと存じます。ご検討いただければ幸いです。」

{reference_data_section}

## 入力

以下のJSON配列は、契約書の修正箇所のリストです。

```json
{changes_json}
```

## 出力形式

必ず以下のJSON配列形式で返してください。各要素は対応する修正箇所へのコメント文字列です。
要素数は入力の変更箇所数と完全に一致させてください。

```json
["コメント1", "コメント2", ...]
```

JSONのみを返してください。前後に説明文は不要です。
"""


def fallback_comment(change: dict) -> str:
    """原文や内部過程を再掲しない安全なフォールバックコメント。"""
    tag = change.get("type", "")
    if tag == "delete":
        return "双方の認識にずれが生じないよう、この記載の削除をご提案します。ご検討いただければ幸いです。"
    elif tag == "insert":
        return "今後の運用を明確にするため、この条項の追加をご提案します。ご検討いただければ幸いです。"
    elif tag == "replace":
        return "双方の認識を明確にするため、文言の調整をご提案します。ご検討いただければ幸いです。"
    return "双方の認識を明確にするため、表現の調整をご提案します。ご検討いただければ幸いです。"


def parse_comments_json(raw: str, expected_count: int) -> List[str]:
    """LLMの応答からコメントJSON配列を抽出・正規化する。"""
    json_match = re.search(r"```json\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            raise ValueError(f"JSON抽出失敗: {raw[:300]}")

    comments = json.loads(json_str)
    if not isinstance(comments, list):
        raise ValueError(f"配列でない応答: {type(comments)}")

    while len(comments) < expected_count:
        comments.append("双方の認識を明確にするため、表現の調整をご提案します。ご検討いただければ幸いです。")

    return [str(c) for c in comments[:expected_count]]


async def generate_strategic_comments(
    original_text: str,
    revised_text: str,
    llm_call: Optional[LLMCallable] = None,
    reference_section: str = "",
) -> List[str]:
    """
    原文と修正後テキストの差分を分析し、Bさんスタイルの戦略的コメントを生成する。
    v3コメントゲート（M8/M13/LEGAL）に違反したコメントは安全なフォールバック文面へ自動置換する。
    """
    clean_revised = _strip_commentary(revised_text)
    if check_injection(f"{original_text}\n{clean_revised}") >= 0.7:
        raise ValueError("契約本文または修正案にプロンプト注入の疑いがあります")
    diffs = para_diff_for_comments(original_text, clean_revised)
    changes = build_changes_summary(diffs)

    if not changes:
        return []

    if llm_call is None:
        return [fallback_comment(c) for c in changes]

    max_batch = 30
    all_comments: List[str] = []

    for batch_start in range(0, len(changes), max_batch):
        batch = changes[batch_start : batch_start + max_batch]
        changes_json = wrap_user_input(json.dumps(batch, ensure_ascii=False, indent=2))
        prompt = COMMENT_GENERATION_PROMPT.replace("{changes_json}", changes_json).replace(
            "{reference_data_section}", reference_section
        )
        try:
            raw = await llm_call(prompt)
            comments = parse_comments_json(raw, len(batch))
            violations = find_comment_violations(comments)
            for violation in violations:
                logger.warning(
                    "v3コメントゲート違反を安全な文面へ置換: %s batch=%d index=%d",
                    violation.code,
                    batch_start,
                    violation.comment_index,
                )
                comments[violation.comment_index] = fallback_comment(batch[violation.comment_index])
            if find_comment_violations(comments):
                raise ValueError("v3コメントゲートの安全置換に失敗しました")
            all_comments.extend(comments)
        except Exception as exc:
            logger.error("コメント生成エラー (batch %d): %s", batch_start, exc)
            for c in batch:
                all_comments.append(fallback_comment(c))

    return all_comments


def build_stage1_revision_prompt(
    *,
    our_company: str,
    their_company: str,
    original_text: str,
    conditions_text: str,
    reference_section: str = "",
    negotiation_win_ratio: str = "",
    negotiation_premises: str = "",
    negotiation_counterpart_profile: str = "",
) -> str:
    """Stage 1: 初期修正プロンプトを構築する。"""
    guarded_original = guard_user_text(original_text, "契約書")
    guarded_conditions = guard_user_text(conditions_text, "前提条件")
    negotiation_sim_block = build_negotiation_sim_block(
        negotiation_win_ratio, negotiation_premises, negotiation_counterpart_profile
    )
    return f"""あなたは{our_company}の担当者です。
{their_company}が作成した契約書を、以下の前提条件に基づいて修正します。

{V3_CONTRACT_GATE}

## 最重要ルール
1. **原案をできるだけ生かしながら最小限の修正に留める**
2. 「ひっくり返すだけ」で済む修正は語順入れ替えのみ
3. 原文を全消去して書き直すのは絶対NG
4. パッと見て結論がわかりやすい修正にする
5. 追加条項は原文の構造・文体に合わせて自然に挿入
6. **契約書の条文のみを出力**（コメント/修正理由/交渉メモは一切含めない）
7. 修正が不要な条文もそのまま残す（契約書全文を出力）

## 必ずチェックすべき重要論点
- A. 本製品の定義（「別紙に定める」のみで将来製品が含まれない解釈になっていないか）
- B. 再販売価格維持（価格拘束が疑われる場合は修正候補とし、法令適用は要弁護士確認）
- C. 製造物責任（PL）（取引実態に応じて確認し、必要なら独立条項候補＝M16）
- D. 保証条件（取引実態に必要か確認）
- E. 用語の首尾一貫性（第1条等で定義された用語を全条文で統一）
- F. 独占販売権と契約期間（投資回収・撤退可能性に応じた自動更新／合意更新の選択）
- G. 受入検査と再検査（再検査も含めた所有権/危険負担移転タイミング）
- H. 既存条項の重複追加禁止（原文に既にある条項を新規追加しない）

{OMISSION_CHECKLIST_14}
{negotiation_sim_block}
## 前提条件

{guarded_conditions}

## 参考データ

{reference_section[:30000]}

## 原文

{guarded_original}

## 出力

修正後の契約書全文のみを出力してください。原案を最大限活かし、最小限の修正のみ行ってください。"""


def build_stage2_self_review_prompt(
    *,
    our_company: str,
    original_text: str,
    conditions_text: str,
    stage1_revised_text: str,
    revision_examples_section: str = "",
) -> str:
    """Stage 2: セルフレビュー（8項目＋網羅性14点）プロンプトを構築する。"""
    guarded_original = guard_user_text(original_text, "契約書")
    guarded_conditions = guard_user_text(conditions_text, "前提条件")
    return f"""あなたは{our_company}の担当者として、初期修正案を作成しました。
以下のチェックリストに照らして自己レビューを行い、不足があれば修正してください。

{V3_CONTRACT_GATE}

## セルフレビュー・チェックリスト（8項目）
1. **再販売価格維持条項の確認**: 価格拘束が疑われる場合は修正候補とし、法令適用は要弁護士確認にする。
2. **PL保険加入義務の確認**: 製造物責任の補償とPL保険の必要性を取引実態から検討し、目的が異なる場合は独立条項候補（M16）にする。
3. **本製品の定義**: 将来製品も含める意図なら明記する。
4. **本製品の保証条件**: 取引実態から必要性を確認し、抜けていれば候補を示す。
5. **用語の首尾一貫性**: 定義された用語（「顧客」「本製品」等）が全条文で統一されているか。
6. **既存条項の重複禁止**: 原文に既にある条項を新規追加で重複していないか。
7. **最小限修正の原則**: 原案を無駄に書き直していないか。
8. **独占販売権と自動更新**: 前提条件と照らして契約期間・更新方式を調整する。

{OMISSION_REVIEW_14}

## 参考実例

{revision_examples_section[:20000]}

## 前提条件

{guarded_conditions}

## 原文

{guarded_original}

## 初期修正案（Stage1出力）

{wrap_context(stage1_revised_text, source='stage1_model_output')}

## 出力

上記8項目のチェックと網羅性14点セルフチェックを全て1件ずつ行い、不足・過剰を修正した**最終版の契約書全文のみ**を出力してください。"""


def build_stage3_consistency_prompt(
    *,
    original_text: str,
    stage2_revised_text: str,
) -> str:
    """Stage 3: 原文整合性チェック（残存条項・参照番号・重複追加・M15欠番偽陽性防止）プロンプトを構築する。"""
    guarded_original = guard_user_text(original_text, "契約書")
    return f"""あなたは契約書の整合性監査人です。
以下の「原文」と「修正案」を比較し、次の7つの致命的な誤りを検出・修正してください。

{V3_CONTRACT_GATE}

## 絶対検出すべき7つの誤り
1. **残存条項リストの破壊的書き換え**: 原文の残存条項に既にある項目を削除せず、条文番号ずれのみ追従更新すること。
2. **参照番号の誤修正**: 条文番号がずれた場合、参照先番号だけを更新し、条文の内容を誤って書き換えないこと。
3. **本製品の定義「別紙に定める」の削除漏れ**: 将来製品を含める修正をした場合、矛盾する限定文言を整理すること。
4. **原文に既にある条項の重複追加**: 相殺、差止請求、契約解除等の重複追加を除去すること。
5. **用語の首尾一貫性**: 定義語が別表現に揺れていないか確認すること。
6. **条文番号の連番ずれ（M15厳守）**: 新設・削除後の連番と参照番号を更新する。ただし抽出テキストの見た目だけで欠番を断定せず、原本表示で確認できない限り番号を勝手に新設・削除しないこと。
7. **再販売価格維持条項の残存**: 法令適用は断定せず、前提条件で採否が確認できなければ要弁護士確認とすること。

## 原文

{guarded_original}

## 修正案（Stage2出力）

{wrap_context(stage2_revised_text, source='stage2_model_output')}

## 出力

上記7項目を全てチェックし、誤りがあれば修正した**最終版の契約書全文のみ**を出力してください。"""
