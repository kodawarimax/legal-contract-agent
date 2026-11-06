"""Legal Contract Review Agent Engine (v3 Unified)."""

from .checklist import (
    CommentViolation,
    OMISSION_CHECKLIST_14,
    OMISSION_REVIEW_14,
    V3_CONTRACT_GATE,
    build_negotiation_sim_block,
    find_comment_violations,
)
from .contract_comments import (
    COMMENT_GENERATION_PROMPT,
    build_stage1_revision_prompt,
    build_stage2_self_review_prompt,
    build_stage3_consistency_prompt,
    fallback_comment,
    generate_strategic_comments,
    parse_comments_json,
    validate_request_options,
)
from .injection_guard import (
    check_injection,
    guard_user_text,
    wrap_context,
    wrap_user_input,
)

__all__ = [
    "CommentViolation",
    "OMISSION_CHECKLIST_14",
    "OMISSION_REVIEW_14",
    "V3_CONTRACT_GATE",
    "build_negotiation_sim_block",
    "find_comment_violations",
    "COMMENT_GENERATION_PROMPT",
    "build_stage1_revision_prompt",
    "build_stage2_self_review_prompt",
    "build_stage3_consistency_prompt",
    "fallback_comment",
    "generate_strategic_comments",
    "parse_comments_json",
    "validate_request_options",
    "check_injection",
    "guard_user_text",
    "wrap_context",
    "wrap_user_input",
]
