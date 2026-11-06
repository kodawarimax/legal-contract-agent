#!/usr/bin/env python3
"""Human-Gated Legal Blog & Article Learning Loop Engine (v3 Unified).

Features:
- Daily/weekly differential ingestion of public legal articles into pending learning cards
- Automatic PII masking (email/phone) and high-entropy secret/token rejection before storage
- Relevance scoring and primary-source verification flagging (e.g., statutory/case-law claims)
- Compact weekly review digest generation (max 4 top candidates x 240 chars) without copying full source_text into pending review JSONs
- Strict human-gated promotion (`promote`) requiring allowlisted approver, SHA-256 hash match, PII review flag, regression PASS, and public authority URL verification when flagged
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOOP_DIR = ROOT / ".runtime" / "blog-learning"
DEFAULT_KNOWLEDGE_DIR = ROOT / "knowledge"
MAX_APPROVED_KNOWLEDGE_CHARS = 95_000
USER_AGENT = "Mozilla/5.0 (compatible; LegalContractAgentBot/3.0)"

DEFAULT_APPROVED_BY = {"legal-reviewer", "admin"}
RELEVANCE_WORDS = {
    "契約", "交渉", "合意", "条項", "行政書士", "法務", "秘密保持", "NDA",
    "著作権", "知的財産", "業務委託", "ライセンス", "販売店", "報酬", "解除",
    "責任", "トラブル", "経営", "AI", "生成AI",
}
VERIFICATION_WORDS = {
    "違法", "無効", "法改正", "最高裁", "判例", "必ず", "絶対", "罰則", "年率",
}
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,4}[-ー‐ ]?\d{1,4}[-ー‐ ]?\d{3,4})(?!\d)")
SECRET_RE = re.compile(r"(?i)(?:api[_ -]?key|secret|token|password)\s*[:=]\s*\S+")
TOKEN_RE = re.compile(r"(?i)\b(?:sk|rk|pk|ghp|github_pat|xox[baprs]|AIza)[-_A-Za-z0-9]{16,}\b")
PUBLIC_PRIMARY_HOSTS = (
    "e-gov.go.jp", "elaws.e-gov.go.jp", "courts.go.jp", "digital.go.jp",
    "meti.go.jp", "moj.go.jp", "ppc.go.jp", "jftc.go.jp", "nta.go.jp",
)
AI_LEGAL_PRIORITY_RE = re.compile(
    r"(?:生成AI|(?<![A-Za-z])AI(?![A-Za-z])|ＡＩ|人工知能|ChatGPT|Chat-GPT|Claude|Gemini|LLM|プロンプト|"
    r"ハルシネーション|ディープフェイク|自動運転|スマートコントラクト|"
    r"不正アクセス|サイバー|セキュリティ|セキュリティー|情報漏洩|個人情報保護|著作権|フリーランス|下請法|独占禁止法|法改正|電子契約|電子帳簿)"
)


@dataclass(frozen=True)
class Paths:
    loop: Path = DEFAULT_LOOP_DIR
    knowledge: Path = DEFAULT_KNOWLEDGE_DIR

    @property
    def state(self) -> Path:
        return self.loop / "state.json"

    @property
    def status(self) -> Path:
        return self.loop / "status.json"

    @property
    def inbox(self) -> Path:
        return self.loop / "inbox"

    @property
    def pending(self) -> Path:
        return self.loop / "pending"

    @property
    def review(self) -> Path:
        return self.loop / "review"


def get_approved_by() -> set[str]:
    env_val = os.environ.get("LEGAL_AGENT_APPROVERS", "").strip()
    if env_val:
        return {item.strip() for item in env_val.split(",") if item.strip()}
    return set(DEFAULT_APPROVED_BY)


def fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, Exception):
        return ""


def extract_entry_ids_from_index(html_text: str) -> list[str]:
    """Extract entry-XXXXXXXXXX numeric IDs from an index HTML page (order-preserving, deduplicated)."""
    ids = re.findall(r"entry-(\d{11,})", html_text)
    seen: set[str] = set()
    result: list[str] = []
    for eid in ids:
        if eid not in seen:
            seen.add(eid)
            result.append(eid)
    return result


def extract_article(html_text: str) -> dict[str, str]:
    """Extract title, date, and plain-text body from an article HTML page."""
    title_m = re.search(r'"headline"\s*:\s*"((?:[^"\\]|\\.)*)"', html_text)
    if not title_m:
        title_m = re.search(r"<title[^>]*>([^<]+)</title>", html_text)
        title = html.unescape(title_m.group(1).strip()) if title_m else "(no title)"
        title = re.sub(r"\s*\|.*$", "", title).strip()
    else:
        title = html.unescape(title_m.group(1))

    date_m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html_text)
    date_str = date_m.group(1) if date_m else ""

    body = ""
    eid_m = re.search(r'data-entry-id="(\d+)"', html_text) or re.search(r"entry-(\d{11,})", html_text)
    if eid_m:
        m = re.search(r'"entry_text"\s*:\s*"((?:[^"\\]|\\.)*)"', html_text, re.DOTALL)
        if m:
            try:
                raw = json.loads('"' + m.group(1) + '"')
            except json.JSONDecodeError:
                raw = m.group(1)
            body = html.unescape(re.sub(r"<[^>]+>", " ", raw))

    if not body:
        body_m = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', html_text)
        if body_m:
            raw = body_m.group(1).replace("\\n", "\n")
            body = html.unescape(re.sub(r"<[^>]+>", " ", raw))

    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return {"title": title, "date": date_str, "body": body}


def read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON: {path}: {exc}") from exc


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def audit(paths: Paths, event: str, entry_id: str, **metadata: object) -> None:
    path = paths.loop / "audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, "entry_id": entry_id, **metadata}, ensure_ascii=False) + "\n")


def load_blog_url(blog_url: str | None = None, profile_path: Path | None = None) -> str:
    url = (blog_url or os.environ.get("LEGAL_AGENT_BLOG_URL", "")).strip()
    if not url and profile_path and profile_path.exists():
        profile = read_json(profile_path, {})
        url = str(profile.get("blog_url", "")).strip()
    url = url.rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("blog_url must be a valid https:// URL")
    return url


def fetch_nonempty(url: str, fetcher: Callable[[str], str] = fetch_url, attempts: int = 2) -> str:
    for attempt in range(attempts):
        body = fetcher(url)
        if body:
            return body
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError(f"public blog fetch failed after {attempts} attempts: {url}")


def sanitize(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []
    if EMAIL_RE.search(text):
        flags.append("email_masked")
        text = EMAIL_RE.sub("[EMAIL_MASKED]", text)
    if PHONE_RE.search(text):
        flags.append("phone_masked")
        text = PHONE_RE.sub("[PHONE_MASKED]", text)
    return text, flags


def looks_like_secret(text: str) -> bool:
    if SECRET_RE.search(text) or TOKEN_RE.search(text):
        return True
    for token in re.findall(r"[A-Za-z0-9_-]{24,}", text):
        counts = [
            sum(char.islower() for char in token),
            sum(char.isupper() for char in token),
            sum(char.isdigit() for char in token),
        ]
        entropy = -sum((count / len(token)) * math.log2(count / len(token)) for count in counts if count)
        if sum(count > 0 for count in counts) >= 3 and entropy > 1.2:
            return True
    return False


def require_safe_approval_text(label: str, text: object) -> str:
    value = str(text or "").strip()
    if not value or EMAIL_RE.search(value) or PHONE_RE.search(value) or looks_like_secret(value):
        raise RuntimeError(f"approval gate failed: unsafe or empty {label}")
    return value


def require_iso_date(label: str, value: object) -> str:
    raw = str(value or "")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError(f"approval gate failed: {label} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != raw:
        raise RuntimeError(f"approval gate failed: {label} must be canonical YYYY-MM-DD")
    return raw


def require_public_primary_url(value: object) -> str:
    url = require_safe_approval_text("primary_source_url", value)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == allowed or host.endswith("." + allowed) for allowed in PUBLIC_PRIMARY_HOSTS):
        raise RuntimeError("approval gate failed: primary source must be an allowlisted public authority")
    return url


def score_article(title: str, body: str) -> tuple[int, list[str], bool]:
    text = f"{title}\n{body}"
    hits = sorted(word for word in RELEVANCE_WORDS if word.lower() in text.lower())
    needs_verification = any(word in text for word in VERIFICATION_WORDS)
    return min(len(hits), 10), hits, needs_verification


def make_card(entry_id: str, url: str, article: dict) -> dict:
    if looks_like_secret(article["title"]) or looks_like_secret(article["body"]):
        raise RuntimeError(f"secret-like content detected before storage: {entry_id}")
    body, privacy_flags = sanitize(article["body"])
    title, title_flags = sanitize(article["title"])
    privacy_flags.extend(flag for flag in title_flags if flag not in privacy_flags)
    score, topics, needs_verification = score_article(title, body)
    return {
        "entry_id": entry_id,
        "source_url": url,
        "title": title,
        "published_at": article["date"],
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "relevance_score": score,
        "topics": topics,
        "privacy_flags": privacy_flags,
        "requires_primary_source_verification": needs_verification,
        "status": "pending_review" if score >= 2 else "hold_low_relevance",
        "excerpt": body[:1200],
        "promotion_rule": "Human summary + intended use + regression PASS + evidence required",
        "source_text": body,
    }


def card_markdown(card: dict) -> str:
    flags = ", ".join(card["privacy_flags"]) or "none"
    topics = ", ".join(card["topics"]) or "none"
    return (
        f"# 学習候補: {card['title']}\n\n"
        f"- entry_id: `{card['entry_id']}`\n"
        f"- source: {card['source_url']}\n"
        f"- published_at: {card['published_at']}\n"
        f"- body_sha256: `{card['body_sha256']}`\n"
        f"- relevance_score: {card['relevance_score']}/10\n"
        f"- topics: {topics}\n"
        f"- privacy: {flags}\n"
        f"- primary_source_verification: {'required' if card['requires_primary_source_verification'] else 'not_flagged'}\n"
        f"- status: **{card['status']}**\n\n"
        "## 確認用抜粋（本番知識ではない）\n\n"
        f"{card['excerpt']}\n"
    )


def pending_card(card: dict) -> dict:
    """Exclude full source_text from weekly review pending cards to prevent context bloat."""
    return {key: value for key, value in card.items() if key != "source_text"}


def normalize_pending(paths: Paths) -> None:
    """Strip source_text from existing pending JSON cards."""
    for path in paths.pending.glob("*.json"):
        card = read_json(path, {})
        if "source_text" in card:
            write_json(path, pending_card(card))


def hydrate_inbox(paths: Paths, fetcher: Callable[[str], str]) -> None:
    """Re-fetch missing source_text in inbox cards only when SHA-256 hash matches."""
    for path in paths.inbox.glob("*.json"):
        card = read_json(path, {})
        if card.get("source_text"):
            continue
        article = extract_article(fetch_nonempty(card["source_url"], fetcher))
        body, _ = sanitize(article.get("body", ""))
        if not body or hashlib.sha256(body.encode()).hexdigest() != card.get("body_sha256"):
            raise RuntimeError(f"inbox hydration hash mismatch: {card.get('entry_id')}")
        card["source_text"] = body
        write_json(path, card)


def detect_ai_legal_tags(title: str, body: str) -> list[str]:
    """Extract AI & emerging legal case keywords without ASCII substring false positives."""
    return sorted(set(AI_LEGAL_PRIORITY_RE.findall(f"{title}\n{body}")))


def update_daily_ai_legal_feed(
    paths: Paths,
    now: datetime,
    *,
    write_active_knowledge: bool = False,
    max_items: int = 12,
) -> Path:
    """Generate a daily AI & emerging legal cases digest from inbox candidates."""
    today_str = now.astimezone(JST).date().isoformat()
    ai_cards: list[dict] = []
    for path in sorted(paths.inbox.glob("*.json")):
        card = read_json(path, {})
        title = str(card.get("title") or "")
        body = str(card.get("source_text") or card.get("excerpt") or "")
        tags = detect_ai_legal_tags(title, body)
        if not tags:
            continue
        card_copy = dict(card)
        card_copy["ai_legal_tags"] = tags
        ai_cards.append(card_copy)

    ai_cards.sort(
        key=lambda c: (str(c.get("published_at") or ""), str(c.get("entry_id") or "")),
        reverse=True,
    )
    selected = ai_cards[:max_items]
    lines = [
        f"# 日次自動更新フィード：AI・新規契約・法務トピック（最終同期: {today_str}）",
        "",
        "※`【※一次資料・要弁護士確認】`が付された記事を含む全ての法務論点は一次整理の参考情報であり、個別案件への法令適用・有効性判断・最終結論は人間および弁護士の確認を必須とする。",
        "",
        f"- 検出AI・新法務記事数: {len(ai_cards)}件（最新{len(selected)}件を表示）",
        "",
    ]
    for card in selected:
        eid = card.get("entry_id", "")
        title = card.get("title", "").strip()
        pub = str(card.get("published_at") or "")[:10]
        url = card.get("source_url", "")
        tags_str = ", ".join(card.get("ai_legal_tags", []))
        verif_flag = "【※一次資料・要弁護士確認】" if card.get("requires_primary_source_verification") else "【実務原則】"
        cleaned_body, _ = sanitize(str(card.get("source_text") or card.get("excerpt") or ""))
        snippet = cleaned_body[:520] + ("…" if len(cleaned_body) > 520 else "")
        lines.extend([
            f"## {verif_flag} {title} ({pub} / `{eid}`)",
            f"- 出典: {url}",
            f"- 検出キーワード: `{tags_str}`",
            f"- 要旨抜粋: {snippet}",
            "",
        ])

    digest_text = "\n".join(lines).strip() + "\n"
    paths.review.mkdir(parents=True, exist_ok=True)
    digest_path = paths.review / "daily-ai-legal-latest.md"
    digest_path.write_text(digest_text, encoding="utf-8")

    if write_active_knowledge and not looks_like_secret(digest_text):
        target = paths.knowledge / "10_daily_ai_legal_feed.md"
        other_chars = sum(
            len(p.read_text(encoding="utf-8"))
            for p in paths.knowledge.rglob("*.md")
            if p.name != "README.md" and p.resolve() != target.resolve()
        )
        if other_chars + len(digest_text) <= MAX_APPROVED_KNOWLEDGE_CHARS:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".md.tmp")
            tmp.write_text(digest_text, encoding="utf-8")
            tmp.replace(target)
    return digest_path


def seed_state(paths: Paths) -> dict:
    legacy = read_json(paths.knowledge / ".blog_manifest.json", {"synced_entry_ids": {}})
    return {
        "seen_entry_ids": sorted(legacy.get("synced_entry_ids", {}).keys()),
        "body_hashes": [],
        "last_success_date": None,
        "success_streak_days": 0,
        "last_run_at": None,
    }


def update_streak(state: dict, now: datetime) -> None:
    today = now.astimezone(JST).date()
    previous = state.get("last_success_date")
    if previous == today.isoformat():
        return
    state["success_streak_days"] = (
        int(state.get("success_streak_days", 0)) + 1
        if previous == (today - timedelta(days=1)).isoformat()
        else 1
    )
    state["last_success_date"] = today.isoformat()


def write_weekly_review(paths: Paths, cards: list[dict], now: datetime) -> Path:
    week = now.astimezone(JST).strftime("%G-W%V")
    report = paths.review / f"weekly-{week}.md"
    pending = [read_json(path, {}) for path in sorted(paths.pending.glob("*.json"))]
    lines = [f"# リーガルAI ブログ学習レビュー {week}", "", f"未審査候補: {len(pending)}件", ""]
    lines.extend([
        "## 優先確認（最大4件・各240文字）",
        "",
        "抜粋は未承認の参考情報です。選出は承認・法的検証を意味しません。",
        "",
    ])
    seen = set()
    for card in sorted(pending, key=lambda item: -(item.get("relevance_score") or 0)):
        identity = card.get("body_sha256") or card.get("entry_id")
        if identity in seen:
            continue
        seen.add(identity)
        lines.extend([
            f"### {card.get('entry_id')} | {card.get('title')}",
            f"出典: {card.get('source_url', '')}",
            f"一次資料確認フラグ: {card.get('requires_primary_source_verification', '不明')}",
            f"本文ハッシュ: {card.get('body_sha256', '不明')}",
            "> " + " ".join(str(card.get("excerpt", "")).split())[:240],
            "",
        ])
        if len(seen) == 4:
            break
    lines.extend(["## 全候補索引（候補の削除・昇格なし）", ""])
    for card in pending:
        lines.append(
            f"- [{card.get('status')}] {card.get('entry_id')} | "
            f"score={card.get('relevance_score')} | {card.get('title')} | "
            f"一次資料確認={card.get('requires_primary_source_verification', '不明')}"
        )
    if cards:
        lines.extend(["", f"今回追加: {len(cards)}件"])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def run(
    paths: Paths,
    fetcher: Callable[[str], str] = fetch_url,
    pages: int = 2,
    now: datetime | None = None,
    blog_url: str | None = None,
    update_ai_feed: bool = True,
) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        state = read_json(paths.state, seed_state(paths))
        seen = set(state.get("seen_entry_ids", []))
        hashes = set(state.get("body_hashes", []))
        resolved_blog_url = load_blog_url(blog_url=blog_url)
        new_ids: list[str] = []
        hydrate_inbox(paths, fetcher)
        for page in range(1, pages + 1):
            url = resolved_blog_url + "/" if page == 1 else f"{resolved_blog_url}/page-{page}.html"
            index = fetch_nonempty(url, fetcher)
            ids = extract_entry_ids_from_index(index)
            if page == 1 and not ids:
                raise RuntimeError("parser drift suspected: no entry IDs on index page 1")
            new_ids.extend(eid for eid in ids if eid not in seen and eid not in new_ids)
            if any(eid in seen for eid in ids):
                break

        cards: list[dict] = []
        for entry_id in new_ids:
            source_url = f"{resolved_blog_url}/entry-{entry_id}.html"
            article = extract_article(fetch_nonempty(source_url, fetcher))
            if not article.get("body"):
                raise RuntimeError(f"empty article body: {entry_id}")
            card = make_card(entry_id, source_url, article)
            if card["body_sha256"] in hashes:
                seen.add(entry_id)
                continue
            paths.inbox.mkdir(parents=True, exist_ok=True)
            paths.pending.mkdir(parents=True, exist_ok=True)
            write_json(paths.inbox / f"{entry_id}.json", card)
            write_json(paths.pending / f"{entry_id}.json", pending_card(card))
            (paths.pending / f"{entry_id}.md").write_text(card_markdown(card), encoding="utf-8")
            seen.add(entry_id)
            hashes.add(card["body_sha256"])
            cards.append(card)

        state.update({
            "seen_entry_ids": sorted(seen),
            "body_hashes": sorted(hashes),
            "last_run_at": now.isoformat(),
        })
        update_streak(state, now)
        write_json(paths.state, state)
        normalize_pending(paths)
        review = write_weekly_review(paths, cards, now)
        ai_digest = update_daily_ai_legal_feed(
            paths,
            now,
            write_active_knowledge=update_ai_feed,
        )
        result = {
            "status": "ok",
            "new_candidates": len(cards),
            "success_streak_days": state["success_streak_days"],
            "weekly_review": str(review),
            "daily_ai_legal_digest": str(ai_digest),
            "action_needed": "review pending candidates; promotion remains human-gated",
            "checked_at": now.isoformat(),
        }
    except Exception as exc:
        result = {
            "status": "failed",
            "error": str(exc),
            "action_needed": "check public blog reachability or parser drift; do not promote",
            "checked_at": now.isoformat(),
        }
    write_json(paths.status, result)
    return result


def _promote(paths: Paths, entry_id: str, approval_file: Path, approved_by_allowlist: set[str] | None = None) -> Path:
    allowlist = approved_by_allowlist or get_approved_by()
    card = read_json(paths.inbox / f"{entry_id}.json", {})
    approval = read_json(approval_file, {})
    if card.get("status") != "pending_review":
        raise RuntimeError("approval gate failed: only pending_review candidates can be promoted")
    if approval.get("approved_by") not in allowlist:
        raise RuntimeError("approval gate failed: approver is not allowlisted")
    approved_at = require_iso_date("approved_at", approval.get("approved_at"))
    summary = require_safe_approval_text("summary", approval.get("summary"))
    intended_use = require_safe_approval_text("intended_use", approval.get("intended_use"))
    evaluation = require_safe_approval_text("evaluation_evidence", approval.get("evaluation_evidence"))
    if approval.get("pii_reviewed") is not True:
        raise RuntimeError("approval gate failed: explicit PII review is required")
    verification = approval.get("primary_source_verification") or {}
    if card.get("requires_primary_source_verification"):
        if not (
            verification.get("checked") is True
            and verification.get("checked_by") in allowlist
        ):
            raise RuntimeError("approval gate failed: primary-source verification evidence is required")
        require_public_primary_url(verification.get("source_url"))
        require_iso_date("primary_source_checked_at", verification.get("checked_at"))
    required = {
        "entry_id": entry_id,
        "body_sha256": card.get("body_sha256"),
        "approved_by": approval.get("approved_by"),
        "approved_at": approved_at,
        "summary": summary,
        "intended_use": intended_use,
        "evaluation_evidence": evaluation,
        "pii_reviewed": True,
        "regression_passed": True,
    }
    if not card or any(not value for value in required.values()) or approval.get("regression_passed") is not True:
        raise RuntimeError("approval gate failed: matching hash, human summary/use, regression PASS and evidence are required")
    if approval.get("entry_id") != entry_id or approval.get("body_sha256") != card.get("body_sha256"):
        raise RuntimeError("approval gate failed: candidate identity/hash mismatch")
    output = (
        "# リーガルAI 承認済みブログ学習要約\n\n"
        f"- 出典: {card['source_url']}\n"
        f"- 公開日: {card['published_at']}\n"
        f"- 承認者: {approval['approved_by']}\n"
        f"- 承認日: {approved_at}\n"
        f"- 用途: {intended_use}\n"
        f"- 回帰評価: {evaluation}\n\n"
        f"## 学習要約\n\n{summary}\n"
    )
    current_chars = sum(
        len(path.read_text(encoding="utf-8"))
        for path in paths.knowledge.rglob("*.md")
        if path.name != "README.md"
    )
    if current_chars + len(output) > MAX_APPROVED_KNOWLEDGE_CHARS:
        raise RuntimeError("promotion blocked: active knowledge would exceed 95,000 chars; compact first")
    target = paths.knowledge / "blog_approved" / f"blog_{entry_id}.md"
    require_safe_approval_text("approved_output", output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target_tmp = target.with_suffix(".md.tmp")
    target_tmp.write_text(output, encoding="utf-8")
    original_card = dict(card)
    pending_json = paths.pending / f"{entry_id}.json"
    pending_md = paths.pending / f"{entry_id}.md"
    try:
        target_tmp.replace(target)
        card["status"] = "promoted"
        write_json(paths.inbox / f"{entry_id}.json", card)
        pending_json.unlink(missing_ok=True)
        pending_md.unlink(missing_ok=True)
        audit(
            paths,
            "promoted",
            entry_id,
            body_sha256=card["body_sha256"],
            approved_by=approval["approved_by"],
            approved_at=approved_at,
            target=str(target),
        )
    except Exception:
        target.unlink(missing_ok=True)
        target_tmp.unlink(missing_ok=True)
        write_json(paths.inbox / f"{entry_id}.json", original_card)
        write_json(pending_json, pending_card(original_card))
        pending_md.write_text(card_markdown(original_card), encoding="utf-8")
        raise
    return target


def promote(paths: Paths, entry_id: str, approval_file: Path, approved_by_allowlist: set[str] | None = None) -> Path:
    try:
        return _promote(paths, entry_id, approval_file, approved_by_allowlist=approved_by_allowlist)
    except Exception as exc:
        try:
            audit(paths, "promotion_rejected", entry_id, reason=str(exc))
        except Exception:
            pass
        raise
