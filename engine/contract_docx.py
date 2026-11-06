"""
契約書の修正案をDOCX（Wordコメント＋赤取消線＋青下線）で出力するサービス。

v3フォーマット準拠:
- WordのTrack Changesは使わず、削除＝赤字(#FF0000)+取消線、追加＝青字(#0000FF)+下線、共通部分＝黒字維持
- 変更箇所にWordコメント（comments.xml）で修正理由を記載
- w:author は必ず提出当事者名（リクエストローカルで分離）
"""
from __future__ import annotations

import difflib
import io
import re
from datetime import datetime, timezone
from typing import List, Tuple

from docx import Document
from docx.enum.text import WD_UNDERLINE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

DEFAULT_COMMENT_AUTHOR = "当社"


def _split_paragraphs(text: str) -> List[str]:
    return [line for line in text.splitlines()]


_COMMENTARY_PATTERNS = re.compile(
    r"^("
    r"【修正理由|【修正サマリ|【交渉メモ|【コメント|【注意|【補足"
    r"|叩き台|以下.*修正|修正サマリー"
    r"|★修正|★追加|★削除"
    r"|─{3,}|━{3,}|＝{3,}"
    r"|・[^　]"
    r")",
    re.MULTILINE,
)


def _strip_commentary(revised_text: str) -> str:
    """AIの修正版出力からコメント・解説部分を除去し、契約書本文のみを返す。"""
    lines = revised_text.splitlines()
    result = []
    in_commentary_block = False

    for line in lines:
        stripped = line.strip()
        if _COMMENTARY_PATTERNS.match(stripped):
            in_commentary_block = True
            continue
        if in_commentary_block:
            if re.match(r"^第\d+条", stripped) or re.match(r"^[（(]\d+[）)]", stripped):
                in_commentary_block = False
            else:
                continue
        result.append(line)

    return "\n".join(result)


def _word_diff(old: str, new: str) -> List[Tuple[str, str]]:
    """日本語でも最小編集になる文字列差分を返す。"""
    sm = difflib.SequenceMatcher(None, old, new)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append(("equal", old[i1:i2]))
        elif tag == "delete":
            ops.append(("delete", old[i1:i2]))
        elif tag == "insert":
            ops.append(("insert", new[j1:j2]))
        elif tag == "replace":
            ops.append(("delete", old[i1:i2]))
            ops.append(("insert", new[j1:j2]))
    return ops


def _para_diff(old_paras: List[str], new_paras: List[str]):
    """段落レベルの差分。各段落について (tag, old_text, new_text) を返す。"""
    sm = difflib.SequenceMatcher(None, old_paras, new_paras)
    result = []
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


def _build_comment_text(tag: str, old_text: str, new_text: str) -> str:
    """変更内容に基づきデフォルトのコメント文を生成する。"""
    if tag == "delete":
        return f"【削除】以下の記載を削除しました。\n原文：「{old_text[:200]}」"
    elif tag == "insert":
        return "【追加】以下の条項を新たに追加しました。"
    elif tag == "replace":
        word_ops = _word_diff(old_text, new_text)
        deleted_parts = [t for op, t in word_ops if op == "delete"]
        inserted_parts = [t for op, t in word_ops if op == "insert"]
        d = "".join(deleted_parts).strip()
        i = "".join(inserted_parts).strip()
        if d and i:
            return f"【修正】「{d[:150]}」→「{i[:150]}」に修正しました。"
        elif d:
            return f"【一部削除】「{d[:200]}」を削除しました。"
        elif i:
            return f"【一部追加】「{i[:200]}」を追加しました。"
        return "【修正】文言を修正しました。"
    return ""


def _add_comment_to_paragraph(
    doc: Document,
    para,
    comment_id: int,
    comment_text: str,
    author: str,
):
    """段落にコメントを追加する（Word / Google Docs 互換）。"""
    range_start = OxmlElement("w:commentRangeStart")
    range_start.set(qn("w:id"), str(comment_id))
    para._p.insert(0, range_start)

    range_end = OxmlElement("w:commentRangeEnd")
    range_end.set(qn("w:id"), str(comment_id))
    para._p.append(range_end)

    ref_run = OxmlElement("w:r")
    ref_rpr = OxmlElement("w:rPr")
    ref_style = OxmlElement("w:rStyle")
    ref_style.set(qn("w:val"), "CommentReference")
    ref_rpr.append(ref_style)
    ref_run.append(ref_rpr)
    comment_ref = OxmlElement("w:commentReference")
    comment_ref.set(qn("w:id"), str(comment_id))
    ref_run.append(comment_ref)
    para._p.append(ref_run)

    _ensure_comments_part(doc, comment_id, comment_text, author)


def _ensure_comments_part(doc: Document, comment_id: int, text: str, author: str):
    """comments.xml パートにコメントを追加（なければ作成）。"""
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from lxml import etree

    wml_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    comments_ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
    comments_rt = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"

    comments_part = None
    for rel in doc.part.rels.values():
        if rel.reltype == comments_rt:
            comments_part = rel.target_part
            break

    if comments_part is None:
        comments_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "</w:comments>"
        )
        comments_part = Part(
            PackURI("/word/comments.xml"),
            comments_ct,
            comments_xml.encode("utf-8"),
            doc.part.package,
        )
        doc.part.relate_to(comments_part, comments_rt)

    root = etree.fromstring(comments_part.blob)
    comment_el = etree.SubElement(root, f"{{{wml_ns}}}comment")
    comment_el.set(f"{{{wml_ns}}}id", str(comment_id))
    comment_el.set(f"{{{wml_ns}}}author", author)
    comment_el.set(
        f"{{{wml_ns}}}date",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    for line in text.split("\n"):
        p_el = etree.SubElement(comment_el, f"{{{wml_ns}}}p")
        r_el = etree.SubElement(p_el, f"{{{wml_ns}}}r")
        t_el = etree.SubElement(r_el, f"{{{wml_ns}}}t")
        t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t_el.text = line

    comments_part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def generate_contract_review_docx(
    original_text: str,
    revised_text: str,
    change_reasons: list[str] | None = None,
    author: str | None = None,
) -> bytes:
    """v3フォーマット準拠: 赤取消線＋青下線＋Wordコメントで修正理由を記載したDOCXを生成。"""
    comment_author = (author or DEFAULT_COMMENT_AUTHOR).strip() or DEFAULT_COMMENT_AUTHOR

    doc = Document()
    style = doc.styles["Normal"]
    font = style.font
    font.name = "MS 明朝"
    font.size = Pt(10.5)

    old_paras = _split_paragraphs(original_text)
    clean_revised = _strip_commentary(revised_text)
    new_paras = _split_paragraphs(clean_revised)
    diffs = _para_diff(old_paras, new_paras)

    comment_id = 0
    reason_idx = 0

    for tag, old_text, new_text in diffs:
        if tag == "equal":
            doc.add_paragraph(old_text)
        elif tag == "delete":
            para = doc.add_paragraph()
            run = para.add_run(old_text)
            run.font.strike = True
            run.font.color.rgb = RGBColor(255, 0, 0)
            comment_id += 1
            if change_reasons and reason_idx < len(change_reasons):
                reason = change_reasons[reason_idx]
                reason_idx += 1
            else:
                reason = _build_comment_text("delete", old_text, "")
            _add_comment_to_paragraph(doc, para, comment_id, reason, comment_author)
        elif tag == "insert":
            para = doc.add_paragraph()
            run = para.add_run(new_text)
            run.font.color.rgb = RGBColor(0, 0, 255)
            run.font.underline = WD_UNDERLINE.SINGLE
            comment_id += 1
            if change_reasons and reason_idx < len(change_reasons):
                reason = change_reasons[reason_idx]
                reason_idx += 1
            else:
                reason = _build_comment_text("insert", "", new_text)
            _add_comment_to_paragraph(doc, para, comment_id, reason, comment_author)
        elif tag == "replace":
            para = doc.add_paragraph()
            for operation, text in _word_diff(old_text, new_text):
                run = para.add_run(text)
                if operation == "delete":
                    run.font.strike = True
                    run.font.color.rgb = RGBColor(255, 0, 0)
                elif operation == "insert":
                    run.font.color.rgb = RGBColor(0, 0, 255)
                    run.font.underline = WD_UNDERLINE.SINGLE
            comment_id += 1
            if change_reasons and reason_idx < len(change_reasons):
                reason = change_reasons[reason_idx]
                reason_idx += 1
            else:
                reason = _build_comment_text("replace", old_text, new_text)
            _add_comment_to_paragraph(doc, para, comment_id, reason, comment_author)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


generate_tracked_changes_docx = generate_contract_review_docx
