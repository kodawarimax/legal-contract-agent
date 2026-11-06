#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_AGENTS_DIR="${HOME}/.claude/agents"
CLAUDE_SKILLS_DIR="${HOME}/.claude/skills"
CODEX_SKILLS_DIR="${HOME}/.codex/skills"

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "${CLAUDE_AGENTS_DIR}/legal-contract-reviewer.md"
  rm -rf "${CLAUDE_SKILLS_DIR}/contract-negotiation-sim" "${CLAUDE_SKILLS_DIR}/contract-quality-gate"
  rm -rf "${CODEX_SKILLS_DIR}/contract-negotiation-sim" "${CODEX_SKILLS_DIR}/contract-quality-gate"
  echo "✅ Legal Contract Review Agent をアンインストールしました。"
  exit 0
fi

echo "⚖️ Legal Contract Review Agent (v3 Unified) をインストールしています..."

mkdir -p "${CLAUDE_AGENTS_DIR}"
cp "${SCRIPT_DIR}/agents/legal-contract-reviewer.md" "${CLAUDE_AGENTS_DIR}/legal-contract-reviewer.md"
echo "  ✓ Claude Code Agent (${CLAUDE_AGENTS_DIR}/legal-contract-reviewer.md) を登録しました"

for target_skills in "${CLAUDE_SKILLS_DIR}" "${CODEX_SKILLS_DIR}"; do
  mkdir -p "${target_skills}/contract-negotiation-sim" "${target_skills}/contract-quality-gate"
  cp "${SCRIPT_DIR}/skills/contract-negotiation-sim/SKILL.md" "${target_skills}/contract-negotiation-sim/SKILL.md"
  cp "${SCRIPT_DIR}/skills/contract-quality-gate/SKILL.md" "${target_skills}/contract-quality-gate/SKILL.md"
done
echo "  ✓ Skills (contract-negotiation-sim / contract-quality-gate) を登録しました"

echo ""
echo "✅ インストール完了！"
echo "----------------------------------------------------------------"
echo "【すぐに試す（デモ実行）】"
echo "  python3 legal_contract_runner.py demo"
echo ""
echo "【コメントのv3違反（M8/M13/LEGAL）を検査する】"
echo "  python3 legal_contract_runner.py check-comments --file comments.json"
echo ""
echo "【赤取消線＋青下線＋Wordコメント付きDOCXを生成する】"
echo "  python3 legal_contract_runner.py redline --original orig.txt --revised rev.txt --author '株式会社サンプル' --output reviewed.docx"
echo "----------------------------------------------------------------"
