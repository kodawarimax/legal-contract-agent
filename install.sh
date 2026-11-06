#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_AGENTS_DIR="${HOME}/.claude/agents"
CLAUDE_SKILLS_DIR="${HOME}/.claude/skills"
CODEX_SKILLS_DIR="${HOME}/.codex/skills"
SKILLS=("contract-negotiation-sim" "contract-quality-gate" "contract-revision-pipeline" "contract-drafting")

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "${CLAUDE_AGENTS_DIR}/legal-contract-reviewer.md"
  for skill in "${SKILLS[@]}"; do
    rm -rf "${CLAUDE_SKILLS_DIR}/${skill}" "${CODEX_SKILLS_DIR}/${skill}"
  done
  echo "✅ Legal Contract Review Agent をアンインストールしました。"
  exit 0
fi

echo "⚖️ Legal Contract Review Agent (v3 Unified) をインストールしています..."

mkdir -p "${CLAUDE_AGENTS_DIR}"
cp "${SCRIPT_DIR}/agents/legal-contract-reviewer.md" "${CLAUDE_AGENTS_DIR}/legal-contract-reviewer.md"
echo "  ✓ Claude Code Agent (${CLAUDE_AGENTS_DIR}/legal-contract-reviewer.md) を登録しました"

for target_skills in "${CLAUDE_SKILLS_DIR}" "${CODEX_SKILLS_DIR}"; do
  for skill in "${SKILLS[@]}"; do
    mkdir -p "${target_skills}/${skill}"
    cp "${SCRIPT_DIR}/skills/${skill}/SKILL.md" "${target_skills}/${skill}/SKILL.md"
  done
done
echo "  ✓ Skills (${SKILLS[*]}) を登録しました"

echo ""
echo "✅ インストール完了！"
echo "----------------------------------------------------------------"
echo "【すぐに試す（デモ実行）】"
echo "  python3 legal_contract_runner.py demo"
echo ""
echo "【3段階契約修正プロンプト（Stage 1/2/3）を生成する】"
echo "  python3 legal_contract_runner.py build-prompt --stage 1 --original orig.txt --conditions cond.txt"
echo ""
echo "【コメントのv3違反（M8/M13/LEGAL）を検査する】"
echo "  python3 legal_contract_runner.py check-comments --file comments.json"
echo ""
echo "【赤取消線＋青下線＋Wordコメント付きDOCXを生成する】"
echo "  python3 legal_contract_runner.py redline --original orig.txt --revised rev.txt --author '株式会社サンプル' --output reviewed.docx"
echo "----------------------------------------------------------------"
