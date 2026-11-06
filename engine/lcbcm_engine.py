"""LCBCMの決定論的なshadow-mode判断カーネル。

ここでのenergyは候補最適化用の数値であり、本人性や宗教的・霊的な
エネルギーを表現または再現するものではない。
"""

from dataclasses import dataclass, fields, replace
from enum import Enum
from hashlib import sha256
from json import dumps
from math import exp, fsum, hypot, isfinite, log, sqrt
from types import MappingProxyType
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

Vector = Tuple[float, ...]


def _digest(payload: object) -> str:
    encoded = dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=repr
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _trace_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else repr(value)
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _trace_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_trace_value(item) for item in value)
    return repr(value)


def _number(
    value: object, name: str, *, lower: float = 0.0, upper: Optional[float] = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result) or result < lower or (upper is not None and result > upper):
        raise ValueError(f"{name} is outside its finite range")
    return float(result)


def _vector(values: Sequence[float], name: str = "vector") -> Vector:
    try:
        result = tuple(float(value) for value in values)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must contain numbers") from error
    length = hypot(*result) if result else 0.0
    if (
        not result
        or not all(isfinite(value) for value in result)
        or not isfinite(length)
        or length == 0
    ):
        raise ValueError(f"{name} must be a non-zero finite vector")
    return result


def _cosine(left: Vector, right: Vector) -> float:
    if len(left) != len(right):
        raise ValueError("vector dimensions must match")
    left_length, right_length = hypot(*left), hypot(*right)
    if (
        not isfinite(left_length)
        or not isfinite(right_length)
        or left_length == 0
        or right_length == 0
    ):
        raise ValueError("vectors must have finite non-zero length")
    result = fsum((a / left_length) * (b / right_length) for a, b in zip(left, right))
    if not isfinite(result):
        raise ValueError("similarity must be finite")
    return max(-1.0, min(1.0, result))


def _similarity(left: Vector, right: Vector) -> float:
    return (1.0 + _cosine(left, right)) / 2.0


def _unit_distance_squared(left: Vector, right: Vector) -> float:
    if len(left) != len(right):
        raise ValueError("vector dimensions must match")
    left_length, right_length = hypot(*left), hypot(*right)
    return fsum((a / left_length - b / right_length) ** 2 for a, b in zip(left, right))


class Status(str, Enum):
    HARD_RULE = "HARD_RULE"
    ASK = "ASK"
    DEFER = "DEFER"
    CONDITIONED = "CONDITIONED"
    YES = "YES"
    NO = "NO"


@dataclass(frozen=True)
class Query:
    query_id: str
    text: str
    semantic_vector: Vector

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "semantic_vector", _vector(self.semantic_vector, "query vector")
        )
        if not self.query_id:
            raise ValueError("query_id is required")


@dataclass(frozen=True)
class Action:
    action_id: str
    label: str
    semantic_vector: Vector
    value_vector: Vector
    required_context: Tuple[str, ...] = ()
    complexity: float = 0.0
    uncertainty: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "semantic_vector",
            _vector(self.semantic_vector, "action semantic vector"),
        )
        object.__setattr__(
            self, "value_vector", _vector(self.value_vector, "action value vector")
        )
        object.__setattr__(self, "complexity", _number(self.complexity, "complexity"))
        object.__setattr__(
            self, "uncertainty", _number(self.uncertainty, "uncertainty", upper=1.0)
        )
        if not self.action_id or any(not key for key in self.required_context):
            raise ValueError("action_id and context keys are required")
        if len(set(self.required_context)) != len(self.required_context):
            raise ValueError("required context keys must be unique")


@dataclass(frozen=True)
class Concept:
    concept_id: str
    label: str
    layer: int
    semantic_vector: Vector
    value_vector: Vector
    parent_id: Optional[str] = None
    gate_key: Optional[str] = None
    gate_value: object = None
    approved: bool = True
    source_case_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "semantic_vector",
            _vector(self.semantic_vector, "concept semantic vector"),
        )
        object.__setattr__(
            self, "value_vector", _vector(self.value_vector, "concept value vector")
        )
        if (
            not self.concept_id
            or self.layer not in (1, 2)
            or type(self.approved) is not bool
        ):
            raise ValueError("concept fields are invalid")
        if (self.gate_key is None) != (self.gate_value is None):
            raise ValueError("concept gates require both key and value")


@dataclass(frozen=True)
class Context:
    values: Mapping[str, object]
    vector: Optional[Vector] = None
    world_state_id: str = "unspecified"

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", dict(self.values))
        if self.vector is not None:
            object.__setattr__(self, "vector", _vector(self.vector, "context vector"))
        if not self.world_state_id:
            raise ValueError("world_state_id is required")


@dataclass(frozen=True)
class Case:
    case_id: str
    situation_vector: Vector
    value_vector: Vector
    cluster_id: Optional[str] = None
    quality_weight: float = 1.0
    relation: str = "support"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "situation_vector",
            _vector(self.situation_vector, "case situation vector"),
        )
        object.__setattr__(
            self, "value_vector", _vector(self.value_vector, "case value vector")
        )
        object.__setattr__(
            self,
            "quality_weight",
            _number(self.quality_weight, "quality_weight", upper=1.0),
        )
        if (
            not self.case_id
            or self.quality_weight == 0
            or self.relation not in ("support", "counterexample")
        ):
            raise ValueError("case_id and positive quality_weight are required")


@dataclass(frozen=True)
class FailureCase:
    failure_id: str
    action_id: str
    context: Context
    severity: float
    reliability: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "severity", _number(self.severity, "severity", upper=1.0)
        )
        object.__setattr__(
            self, "reliability", _number(self.reliability, "reliability", upper=1.0)
        )
        if not self.failure_id or not self.action_id:
            raise ValueError("failure IDs are required")


ConstraintPredicate = Callable[[Context, Optional[Action]], bool]


@dataclass(frozen=True)
class Constraint:
    constraint_id: str
    predicate: ConstraintPredicate
    phase: str = "pre"
    context_keys: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.constraint_id
            or self.phase not in ("pre", "post")
            or not callable(self.predicate)
        ):
            raise ValueError("constraint fields are invalid")


@dataclass(frozen=True)
class RiskRule:
    action_id: str
    context_key: str
    equals: object
    harm: float = 0.0
    autonomy_violation: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "harm", _number(self.harm, "harm", upper=1.0))
        object.__setattr__(
            self,
            "autonomy_violation",
            _number(self.autonomy_violation, "autonomy_violation", upper=1.0),
        )
        if not self.action_id or not self.context_key:
            raise ValueError("risk rule keys are required")


@dataclass(frozen=True)
class HarmonyRule:
    action_id: str
    context_key: str
    equals: object
    tolerance_penalty: float = 0.0
    nobility_penalty: float = 0.0
    connection_penalty: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "tolerance_penalty",
            "nobility_penalty",
            "connection_penalty",
        ):
            object.__setattr__(
                self, name, _number(getattr(self, name), name, upper=1.0)
            )
        if not self.action_id or not self.context_key:
            raise ValueError("Harmony rule keys are required")


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    approver_id: str
    approved: bool
    candidate_digest: str
    evidence_digest: str

    def __post_init__(self) -> None:
        if (
            not self.approval_id
            or not self.approver_id
            or type(self.approved) is not bool
            or len(self.candidate_digest) != 64
            or len(self.evidence_digest) != 64
        ):
            raise ValueError("typed approval fields are required")


def digest_concept(
    concept: Concept,
    *,
    manifest_version: str = "",
    concept_package_version: str = "",
) -> str:
    return _digest(
        {
            "concept_id": concept.concept_id,
            "label": concept.label,
            "layer": concept.layer,
            "semantic_vector": concept.semantic_vector,
            "value_vector": concept.value_vector,
            "parent_id": concept.parent_id,
            "gate_key": concept.gate_key,
            "gate_value": concept.gate_value,
            "approved": concept.approved,
            "source_case_ids": concept.source_case_ids,
            "manifest_version": manifest_version,
            "concept_package_version": concept_package_version,
        }
    )


@dataclass(frozen=True)
class PromotionEvidence:
    cases: Tuple[Case, ...]
    failure_case_ids: Tuple[str, ...]
    counterexample_case_ids: Tuple[str, ...]
    v_path: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "v_path", _number(self.v_path, "v_path", upper=1.0))
        if not self.cases:
            raise ValueError("promotion evidence requires cases")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("promotion case IDs must be unique")
        if len(set(self.failure_case_ids)) != len(self.failure_case_ids):
            raise ValueError("failure evidence IDs must be unique")
        if len(set(self.counterexample_case_ids)) != len(self.counterexample_case_ids):
            raise ValueError("counterexample evidence IDs must be unique")

    def digest(self) -> str:
        return _digest(
            {
                "cases": [
                    {
                        "case_id": case.case_id,
                        "situation_vector": case.situation_vector,
                        "value_vector": case.value_vector,
                        "cluster_id": case.cluster_id,
                        "quality_weight": case.quality_weight,
                        "relation": case.relation,
                    }
                    for case in self.cases
                ],
                "failure_case_ids": self.failure_case_ids,
                "counterexample_case_ids": self.counterexample_case_ids,
                "v_path": self.v_path,
            }
        )


@dataclass(frozen=True)
class PromotionThresholds:
    n_eff: float = 1.0
    failure_coverage: float = 0.5
    counterexample_coverage: float = 0.5
    v_path: float = 0.5

    def __post_init__(self) -> None:
        object.__setattr__(self, "n_eff", _number(self.n_eff, "promotion n_eff"))
        for name in ("failure_coverage", "counterexample_coverage", "v_path"):
            object.__setattr__(
                self, name, _number(getattr(self, name), name, upper=1.0)
            )


@dataclass(frozen=True)
class DomainManifest:
    version: str
    vector_dimension: int
    actions: Tuple[Action, ...]
    concepts: Tuple[Concept, ...]
    parameter_version: str = "p1"
    concept_package_version: str = "c1"
    authorized_approver_ids: Tuple[str, ...] = ()
    risk_rules: Tuple[RiskRule, ...] = ()
    harmony_rules: Tuple[HarmonyRule, ...] = ()
    formula_version: str = "lcbcm-mvp-1"

    def __post_init__(self) -> None:
        if (
            not self.version
            or not self.parameter_version
            or not self.concept_package_version
            or type(self.vector_dimension) is not int
            or self.vector_dimension <= 0
            or not 1 <= len(self.actions) <= 5
        ):
            raise ValueError("manifest fields are invalid")
        action_ids = [action.action_id for action in self.actions]
        concept_ids = [concept.concept_id for concept in self.concepts]
        if len(set(action_ids)) != len(action_ids) or len(set(concept_ids)) != len(
            concept_ids
        ):
            raise ValueError("action and concept IDs must be unique")
        vectors = [
            vector
            for action in self.actions
            for vector in (action.semantic_vector, action.value_vector)
        ] + [
            vector
            for concept in self.concepts
            for vector in (concept.semantic_vector, concept.value_vector)
        ]
        if any(len(vector) != self.vector_dimension for vector in vectors):
            raise ValueError("all vectors must match the manifest dimension")
        concepts = {concept.concept_id: concept for concept in self.concepts}
        seen_gates = set()
        for concept in self.concepts:
            if concept.layer == 2:
                if concept.parent_id is not None or concept.gate_key is not None:
                    raise ValueError("L2 concepts cannot have a parent or gate")
                continue
            parent = concepts.get(concept.parent_id or "")
            if parent is None or parent.layer != 2 or concept.gate_key is None:
                raise ValueError("L1 concepts require an L2 parent and a gate")
            if concept.approved and not parent.approved:
                raise ValueError("an approved child cannot have an unapproved parent")
            try:
                gate = (concept.parent_id, concept.gate_key, concept.gate_value)
                hash(gate)
            except TypeError as error:
                raise ValueError("gate values must be hashable") from error
            if gate in seen_gates:
                raise ValueError("context gates must be unique within a parent")
            seen_gates.add(gate)
        if any(rule.action_id not in action_ids for rule in self.risk_rules):
            raise ValueError("risk rules must reference approved manifest actions")
        if any(rule.action_id not in action_ids for rule in self.harmony_rules):
            raise ValueError("Harmony rules must reference approved manifest actions")
        if len(set(self.authorized_approver_ids)) != len(self.authorized_approver_ids):
            raise ValueError("authorized approver IDs must be unique")


@dataclass(frozen=True)
class RetrievedConcept:
    concept_id: str
    semantic_fit: float


@dataclass(frozen=True)
class CandidateBundle:
    candidates: Tuple[RetrievedConcept, ...]
    failure_case_ids: Tuple[str, ...]


@dataclass(frozen=True)
class WeightedConceptSet:
    alpha_by_concept: Mapping[str, float]


@dataclass(frozen=True)
class ScoredAction:
    action_id: str
    energy: float
    contributions: Mapping[str, float]
    failure_case_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Verification:
    v_path: float
    passed: bool
    layer_scores: Tuple[float, ...]


@dataclass(frozen=True)
class DecisionTrace:
    formula_version: str
    parameter_version: str
    concept_package_version: str
    manifest_version: str
    source_record_ids: Tuple[str, ...]
    failure_case_ids: Tuple[str, ...]
    pre_constraint_result: Tuple[str, ...]
    post_constraint_results_by_action: Mapping[str, Tuple[str, ...]]
    context_used: Mapping[str, object]
    missing_context: Tuple[str, ...]
    world_state_id: str
    active_concepts_with_alpha: Mapping[str, float]
    selected_parent_id: Optional[str]
    selected_concept_id: Optional[str]
    gate: Optional[Tuple[str, object]]
    action_contributions: Mapping[str, Mapping[str, float]]
    action_energies: Mapping[str, float]
    selected_action_id: Optional[str]
    selected_action_energy: Optional[float]
    runner_up_id: Optional[str]
    runner_up_energy: Optional[float]
    score_gap: Optional[float]
    v_path: Optional[float]
    uncertainty: Optional[float]
    handoff_reason: Optional[str]
    trace_digest: str


def _plain_trace_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_trace_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_plain_trace_value(item) for item in value)
    return value


def digest_decision_trace(trace: DecisionTrace) -> str:
    payload = {
        item.name: _plain_trace_value(getattr(trace, item.name))
        for item in fields(trace)
        if item.name != "trace_digest"
    }
    return _digest(payload)


def verify_trace_digest(trace: DecisionTrace) -> bool:
    return trace.trace_digest == digest_decision_trace(trace)


@dataclass(frozen=True)
class Decision:
    status: Status
    action_id: Optional[str]
    trace: DecisionTrace


def _clusters(cases: Sequence[Case]) -> Dict[object, Tuple[Case, ...]]:
    grouped: Dict[object, list[Case]] = {}
    for case in cases:
        key: object = (
            ("cluster", case.cluster_id)
            if case.cluster_id
            else ("exact", case.situation_vector, case.value_vector)
        )
        grouped.setdefault(key, []).append(case)
    return {key: tuple(items) for key, items in grouped.items()}


def _weighted_vector(cases: Sequence[Case], attribute: str) -> Vector:
    total = fsum(case.quality_weight for case in cases)
    first = getattr(cases[0], attribute)
    return tuple(
        fsum(getattr(case, attribute)[index] * case.quality_weight for case in cases)
        / total
        for index in range(len(first))
    )


def _logdet_identity_plus(
    kernel: Sequence[Sequence[float]], kappa: float = 1.0
) -> float:
    """Cholesky分解でlog det(I + kappa*K)を安定計算する。"""
    size = len(kernel)
    lower = [[0.0] * size for _ in range(size)]
    for row in range(size):
        for column in range(row + 1):
            value = (1.0 if row == column else 0.0) + kappa * kernel[row][column]
            value -= fsum(
                lower[row][index] * lower[column][index] for index in range(column)
            )
            if row == column:
                if value <= 0 or not isfinite(value):
                    raise ValueError("diversity kernel must be positive definite")
                lower[row][column] = sqrt(value)
            else:
                lower[row][column] = value / lower[column][column]
    return 2.0 * fsum(log(lower[index][index]) for index in range(size))


def effective_sample_size(cases: Sequence[Case]) -> float:
    """オフラインcluster_id、品質飽和、Kish ESS、文脈多様性を統合する。"""
    if not cases:
        return 0.0
    grouped = _clusters(cases)
    weights = [
        min(1.0, fsum(case.quality_weight for case in items))
        for items in grouped.values()
    ]
    kish = fsum(weights) ** 2 / fsum(weight * weight for weight in weights)
    if len(grouped) == 1:
        return 1.0
    centroids = [
        _weighted_vector(items, "situation_vector") for items in grouped.values()
    ]
    kernel = [
        [exp(-_unit_distance_squared(left, right) / 2.0) for right in centroids]
        for left in centroids
    ]
    diversity = min(1.0, _logdet_identity_plus(kernel) / (len(kernel) * log(2.0)))
    result = max(1.0, min(kish, kish * diversity))
    if not isfinite(result):
        raise ValueError("effective sample size must be finite")
    return float(result)


class LCBMEngine:
    def __init__(
        self,
        manifest: DomainManifest,
        constraints: Sequence[Constraint] = (),
        failure_cases: Sequence[FailureCase] = (),
        *,
        sigma: float = 1.0,
        verification_threshold: float = 0.25,
        retrieval_margin: float = 0.0,
        decision_margin: float = 0.05,
        uncertainty_threshold: float = 0.5,
        lambda_v: float = 1.0,
        lambda_h: float = 1.0,
        lambda_a: float = 1.0,
        lambda_f: float = 1.0,
        lambda_u: float = 1.0,
        lambda_k: float = 1.0,
        promotion_thresholds: PromotionThresholds = PromotionThresholds(),
    ):
        self.manifest = manifest
        self.constraints = tuple(constraints)
        self.failure_cases = tuple(failure_cases)
        self.sigma = _number(sigma, "sigma")
        if self.sigma == 0:
            raise ValueError("sigma must be positive")
        self.verification_threshold = _number(
            verification_threshold, "verification_threshold", upper=1.0
        )
        self.retrieval_margin = _number(retrieval_margin, "retrieval_margin", upper=1.0)
        self.decision_margin = _number(decision_margin, "decision_margin")
        self.uncertainty_threshold = _number(
            uncertainty_threshold, "uncertainty_threshold", upper=1.0
        )
        self.weights = {
            "value_alignment": _number(lambda_v, "lambda_v"),
            "harm": _number(lambda_h, "lambda_h"),
            "autonomy": _number(lambda_a, "lambda_a"),
            "failure": _number(lambda_f, "lambda_f"),
            "uncertainty": _number(lambda_u, "lambda_u"),
            "complexity": _number(lambda_k, "lambda_k"),
        }
        self.promotion_thresholds = promotion_thresholds
        self.concepts = {concept.concept_id: concept for concept in manifest.concepts}
        self.actions = {action.action_id: action for action in manifest.actions}
        self.concept_package_version = manifest.concept_package_version
        self._promotion_count = 0
        self._used_approval_ids: set[str] = set()
        if len({constraint.constraint_id for constraint in self.constraints}) != len(
            self.constraints
        ):
            raise ValueError("constraint IDs must be unique")
        if len({failure.failure_id for failure in self.failure_cases}) != len(
            self.failure_cases
        ):
            raise ValueError("failure IDs must be unique")
        for failure in self.failure_cases:
            if (
                failure.action_id not in self.actions
                or failure.context.vector is None
                or len(failure.context.vector) != manifest.vector_dimension
            ):
                raise ValueError(
                    "failure cases require a known action and correctly sized context"
                )

    def candidate_digest(self, candidate: Concept) -> str:
        return digest_concept(
            candidate,
            manifest_version=self.manifest.version,
            concept_package_version=self.concept_package_version,
        )

    def abstract_cases(self, cases: Sequence[Case]) -> Tuple[Concept, ...]:
        if not cases:
            raise ValueError("at least one case is required")
        if any(
            len(case.situation_vector) != self.manifest.vector_dimension
            or len(case.value_vector) != self.manifest.vector_dimension
            for case in cases
        ):
            raise ValueError("case dimensions must match manifest")
        support_cases = tuple(case for case in cases if case.relation == "support")
        if not support_cases:
            raise ValueError("abstraction requires at least one support case")
        grouped = tuple(_clusters(support_cases).values())
        cluster_weights = tuple(
            min(1.0, fsum(case.quality_weight for case in items)) for items in grouped
        )
        total = fsum(cluster_weights)
        situations = tuple(
            _weighted_vector(items, "situation_vector") for items in grouped
        )
        values = tuple(_weighted_vector(items, "value_vector") for items in grouped)
        semantic_vector = tuple(
            fsum(
                vector[index] * weight
                for vector, weight in zip(situations, cluster_weights)
            )
            / total
            for index in range(self.manifest.vector_dimension)
        )
        value_vector = tuple(
            fsum(
                vector[index] * weight
                for vector, weight in zip(values, cluster_weights)
            )
            / total
            for index in range(self.manifest.vector_dimension)
        )
        source_ids = tuple(sorted({case.case_id for case in support_cases}))
        return (
            Concept(
                "abstract-candidate",
                "abstracted candidate",
                2,
                semantic_vector,
                value_vector,
                approved=False,
                source_case_ids=source_ids,
            ),
        )

    def retrieve(self, query: Query) -> CandidateBundle:
        if len(query.semantic_vector) != self.manifest.vector_dimension:
            raise ValueError("query dimensions must match manifest")
        candidates = tuple(
            RetrievedConcept(
                concept.concept_id,
                _similarity(query.semantic_vector, concept.semantic_vector),
            )
            for concept in self.concepts.values()
            if concept.layer == 2 and concept.approved is True
        )
        ordered = tuple(
            sorted(candidates, key=lambda item: (-item.semantic_fit, item.concept_id))
        )
        return CandidateBundle(
            ordered, tuple(sorted(failure.failure_id for failure in self.failure_cases))
        )

    def activate(self, bundle: CandidateBundle, context: Context) -> WeightedConceptSet:
        """MVP縮約En: 意味適合、承認、文脈ゲート、失敗密度を積で適用する。"""
        failure_gate = 1.0 / (1.0 + len(bundle.failure_case_ids))
        active: Dict[str, float] = {}
        for candidate in bundle.candidates:
            parent = self.concepts.get(candidate.concept_id)
            child = self.aufheben(candidate.concept_id, context)
            if parent is None or parent.approved is not True or child is None:
                continue
            alpha = candidate.semantic_fit * failure_gate
            active[parent.concept_id] = alpha
            active[child.concept_id] = alpha
        return WeightedConceptSet(MappingProxyType(active))

    def aufheben(self, parent_id: str, context: Context) -> Optional[Concept]:
        parent = self.concepts.get(parent_id)
        if parent is None or parent.layer != 2 or parent.approved is not True:
            return None
        matches = [
            concept
            for concept in self.concepts.values()
            if concept.layer == 1
            and concept.parent_id == parent_id
            and concept.approved is True
            and concept.gate_key is not None
            and context.values.get(concept.gate_key) == concept.gate_value
        ]
        if len(matches) > 1:
            raise ValueError("ambiguous context gate")
        return matches[0] if matches else None

    def concretize(self, concept_id: str, context: Context) -> Tuple[ScoredAction, ...]:
        concept = self.concepts.get(concept_id)
        if concept is None or concept.layer != 1 or concept.approved is not True:
            raise ValueError("concretize requires an approved L1 concept")
        if (
            context.vector is not None
            and len(context.vector) != self.manifest.vector_dimension
        ):
            raise ValueError("context dimensions must match manifest")
        if self.failure_cases and context.vector is None:
            raise ValueError("context vector is required when failure knowledge exists")
        scored: list[ScoredAction] = []
        for action in self.actions.values():
            support = _similarity(concept.semantic_vector, action.semantic_vector)
            alignment = _cosine(concept.value_vector, action.value_vector)
            harm, autonomy = self._risk(action.action_id, context)
            failure, failure_ids = self._failure_penalty(action.action_id, context)
            contributions = {
                "support": -support,
                "value_alignment": -self.weights["value_alignment"] * alignment,
                "harm": self.weights["harm"] * harm,
                "autonomy": self.weights["autonomy"] * autonomy,
                "failure": self.weights["failure"] * failure,
                "uncertainty": self.weights["uncertainty"] * action.uncertainty,
                "complexity": self.weights["complexity"] * action.complexity,
            }
            energy = fsum(contributions.values())
            if not isfinite(energy):
                raise ValueError("action energy must be finite")
            scored.append(
                ScoredAction(
                    action.action_id,
                    energy,
                    MappingProxyType(contributions),
                    failure_ids,
                )
            )
        return tuple(sorted(scored, key=lambda item: (item.energy, item.action_id)))

    def verify(
        self, parent_id: str, child_id: str, scored_action: ScoredAction
    ) -> Verification:
        parent, child = self.concepts.get(parent_id), self.concepts.get(child_id)
        action = self.actions.get(scored_action.action_id)
        if (
            parent is None
            or child is None
            or action is None
            or parent.approved is not True
            or child.approved is not True
            or child.parent_id != parent_id
        ):
            raise ValueError("verification path is unknown or unapproved")
        parent_child_forward = _similarity(
            parent.semantic_vector, child.semantic_vector
        )
        parent_child_backward = _similarity(
            child.semantic_vector, parent.semantic_vector
        )
        child_action_forward = _similarity(
            child.semantic_vector, action.semantic_vector
        )
        child_action_backward = _similarity(
            action.semantic_vector, child.semantic_vector
        )
        layer_scores = (
            sqrt(parent_child_forward * parent_child_backward),
            sqrt(child_action_forward * child_action_backward),
        )
        v_path = min(layer_scores)
        return Verification(v_path, v_path >= self.verification_threshold, layer_scores)

    def decide(self, query: Query, context: Context) -> Decision:
        if (
            not isinstance(query, Query)
            or len(query.semantic_vector) != self.manifest.vector_dimension
        ):
            raise ValueError("a correctly sized Query is required")
        pre = self._check_constraints("pre", context, None)
        if pre:
            return self._decision(
                Status.HARD_RULE, context, pre=pre, handoff="pre hard constraint failed"
            )
        required = self._required_context_keys(context)
        missing = tuple(sorted(key for key in required if key not in context.values))
        if missing:
            return self._decision(
                Status.ASK,
                context,
                pre=pre,
                missing=missing,
                handoff="required context is missing",
            )
        if self.failure_cases and context.vector is None:
            return self._decision(
                Status.ASK,
                context,
                pre=pre,
                missing=("__context_vector__",),
                handoff="failure knowledge requires a context vector",
            )
        bundle = self.retrieve(query)
        weighted = self.activate(bundle, context)
        active = dict(weighted.alpha_by_concept)
        applicable = [
            (
                self.concepts[candidate.concept_id],
                active[candidate.concept_id],
                self.aufheben(candidate.concept_id, context),
            )
            for candidate in bundle.candidates
            if candidate.concept_id in active
        ]
        applicable = [item for item in applicable if item[2] is not None]
        if not applicable:
            return self._decision(
                Status.DEFER,
                context,
                pre=pre,
                active=active,
                handoff="no approved contextual concept path",
            )
        applicable.sort(key=lambda item: (-item[1], item[0].concept_id))
        if (
            len(applicable) > 1
            and applicable[0][1] - applicable[1][1] <= self.retrieval_margin
        ):
            return self._decision(
                Status.ASK,
                context,
                pre=pre,
                active=active,
                handoff="concept retrieval is ambiguous",
            )
        parent, parent_alpha, maybe_child = applicable[0]
        if maybe_child is None:  # Narrowing for type checkers; filtered above.
            raise RuntimeError("unreachable contextual path")
        child = maybe_child
        active[child.concept_id] = parent_alpha
        base_scored = self.concretize(child.concept_id, context)
        post = {
            item.action_id: self._check_constraints(
                "post", context, self.actions[item.action_id]
            )
            for item in base_scored
        }
        safe_base = [item for item in base_scored if not post[item.action_id]]
        if not safe_base:
            return self._decision(
                Status.DEFER,
                context,
                pre=pre,
                post=post,
                active=active,
                parent=parent,
                child=child,
                scored=base_scored,
                handoff="all actions failed post constraints",
            )
        brand_required = self._harmony_context_keys(safe_base, context)
        brand_missing = tuple(
            key for key in brand_required if key not in context.values
        )
        if brand_missing:
            return self._decision(
                Status.ASK,
                context,
                pre=pre,
                post=post,
                missing=brand_missing,
                active=active,
                parent=parent,
                child=child,
                scored=base_scored,
                handoff="Harmony context is missing for safe actions",
            )
        safe = self._apply_harmony(safe_base, context)
        safe_by_id = {item.action_id: item for item in safe}
        scored = tuple(
            safe_by_id.get(item.action_id, item) for item in base_scored
        )
        verified = [
            (item, self.verify(parent.concept_id, child.concept_id, item))
            for item in safe
        ]
        passed = [(item, report) for item, report in verified if report.passed]
        if not passed:
            best_report = max(
                (report for _, report in verified), key=lambda report: report.v_path
            )
            return self._decision(
                Status.DEFER,
                context,
                pre=pre,
                post=post,
                active=active,
                parent=parent,
                child=child,
                scored=scored,
                verification=best_report,
                handoff="verification path below threshold",
            )
        passed.sort(key=lambda item: (item[0].energy, item[0].action_id))
        selected, verification = passed[0]
        runner_up = passed[1][0] if len(passed) > 1 else None
        gap = runner_up.energy - selected.energy if runner_up else None
        status = Status.YES
        if (gap is not None and gap < self.decision_margin) or self.actions[
            selected.action_id
        ].uncertainty > self.uncertainty_threshold:
            status = Status.CONDITIONED
        return self._decision(
            status,
            context,
            pre=pre,
            post=post,
            active=active,
            parent=parent,
            child=child,
            scored=scored,
            selected=selected,
            runner_up=runner_up,
            verification=verification,
        )

    def promote(
        self,
        candidate: Concept,
        approval: ApprovalRecord,
        evidence: PromotionEvidence,
    ) -> Concept:
        if not isinstance(approval, ApprovalRecord):
            raise TypeError("a typed ApprovalRecord is required")
        if not isinstance(evidence, PromotionEvidence):
            raise TypeError("typed PromotionEvidence is required")
        if approval.approval_id in self._used_approval_ids:
            raise ValueError("approval records are single-use")
        if approval.approver_id not in self.manifest.authorized_approver_ids:
            raise ValueError("approver is not authorized for this manifest")
        if approval.approved is not True:
            raise ValueError("authorized human approval is required")
        if approval.candidate_digest != self.candidate_digest(candidate):
            raise ValueError("approval is not bound to this candidate")
        if approval.evidence_digest != evidence.digest():
            raise ValueError("approval is not bound to this evidence")
        evidence_case_ids = {case.case_id for case in evidence.cases}
        if not set(candidate.source_case_ids).issubset(evidence_case_ids):
            raise ValueError("candidate sources are not present in promotion evidence")
        known_failures = {failure.failure_id for failure in self.failure_cases}
        provided_failures = set(evidence.failure_case_ids)
        if not provided_failures.issubset(known_failures):
            raise ValueError("promotion evidence references unknown failures")
        known_counterexamples = {
            case.case_id for case in evidence.cases if case.relation == "counterexample"
        }
        provided_counterexamples = set(evidence.counterexample_case_ids)
        if not provided_counterexamples.issubset(known_counterexamples):
            raise ValueError("promotion evidence references unknown counterexamples")
        n_eff = effective_sample_size(evidence.cases)
        failure_coverage = (
            1.0 if not known_failures else len(provided_failures) / len(known_failures)
        )
        counterexample_coverage = (
            len(provided_counterexamples) / len(known_counterexamples)
            if known_counterexamples
            else 0.0
        )
        thresholds = self.promotion_thresholds
        if (
            n_eff < thresholds.n_eff
            or failure_coverage < thresholds.failure_coverage
            or counterexample_coverage < thresholds.counterexample_coverage
            or evidence.v_path < thresholds.v_path
        ):
            raise ValueError("promotion evidence is below threshold")
        if candidate.approved is True or candidate.concept_id in self.concepts:
            raise ValueError("candidate must be new and unapproved")
        if (
            len(candidate.semantic_vector) != self.manifest.vector_dimension
            or len(candidate.value_vector) != self.manifest.vector_dimension
        ):
            raise ValueError("candidate dimensions must match manifest")
        promoted = replace(candidate, approved=True)
        self.concepts[promoted.concept_id] = promoted
        self._used_approval_ids.add(approval.approval_id)
        self._promotion_count += 1
        self.concept_package_version = (
            f"{self.manifest.concept_package_version}+p{self._promotion_count}"
        )
        return promoted

    def _required_context_keys(
        self, context: Optional[Context] = None
    ) -> Tuple[str, ...]:
        keys = {
            key for action in self.actions.values() for key in action.required_context
        }
        keys.update(
            concept.gate_key
            for concept in self.concepts.values()
            if concept.approved is True and concept.gate_key is not None
        )
        keys.update(rule.context_key for rule in self.manifest.risk_rules)
        if self.manifest.harmony_rules:
            keys.add("brand_relevance")
        keys.update(
            key for constraint in self.constraints for key in constraint.context_keys
        )
        return tuple(sorted(keys))

    def _risk(self, action_id: str, context: Context) -> Tuple[float, float]:
        matched = [
            rule
            for rule in self.manifest.risk_rules
            if rule.action_id == action_id
            and context.values.get(rule.context_key) == rule.equals
        ]
        return (
            min(1.0, fsum(rule.harm for rule in matched)),
            min(1.0, fsum(rule.autonomy_violation for rule in matched)),
        )

    def _harmony_penalties(
        self, action_id: str, context: Context
    ) -> Tuple[float, float, float]:
        relevance = _number(
            context.values.get("brand_relevance"), "brand_relevance", upper=1.0
        )
        matched = [
            rule
            for rule in self.manifest.harmony_rules
            if rule.action_id == action_id
            and context.values.get(rule.context_key) == rule.equals
        ]
        return (
            relevance
            * min(1.0, fsum(rule.tolerance_penalty for rule in matched)),
            relevance * min(1.0, fsum(rule.nobility_penalty for rule in matched)),
            relevance
            * min(1.0, fsum(rule.connection_penalty for rule in matched)),
        )

    def _harmony_context_keys(
        self, scored: Sequence[ScoredAction], context: Context
    ) -> Tuple[str, ...]:
        if not self.manifest.harmony_rules:
            return ()
        relevance = _number(
            context.values.get("brand_relevance"), "brand_relevance", upper=1.0
        )
        if relevance == 0.0:
            return ()
        action_ids = {item.action_id for item in scored}
        return tuple(
            sorted(
                {
                    rule.context_key
                    for rule in self.manifest.harmony_rules
                    if rule.action_id in action_ids
                }
            )
        )

    def _apply_harmony(
        self, scored: Sequence[ScoredAction], context: Context
    ) -> Tuple[ScoredAction, ...]:
        if not self.manifest.harmony_rules:
            return tuple(scored)
        branded = []
        for item in scored:
            tolerance, nobility, connection = self._harmony_penalties(
                item.action_id, context
            )
            contributions = dict(item.contributions)
            contributions.update(
                {
                    "harmony_tolerance": tolerance,
                    "harmony_nobility": nobility,
                    "harmony_connection": connection,
                }
            )
            energy = fsum(contributions.values())
            if not isfinite(energy):
                raise ValueError("Harmony action energy must be finite")
            branded.append(
                replace(
                    item,
                    energy=energy,
                    contributions=MappingProxyType(contributions),
                )
            )
        return tuple(sorted(branded, key=lambda item: (item.energy, item.action_id)))

    def _failure_penalty(
        self, action_id: str, context: Context
    ) -> Tuple[float, Tuple[str, ...]]:
        if context.vector is None:
            return 0.0, ()
        penalty = 0.0
        used = []
        for failure in self.failure_cases:
            if failure.action_id != action_id or failure.context.vector is None:
                continue
            distance = _unit_distance_squared(context.vector, failure.context.vector)
            contribution = (
                failure.severity
                * failure.reliability
                * exp(-distance / (2.0 * self.sigma**2))
            )
            if contribution > 0:
                used.append(failure.failure_id)
                penalty += contribution
        if not isfinite(penalty):
            raise ValueError("failure penalty must be finite")
        return penalty, tuple(sorted(used))

    def _check_constraints(
        self, phase: str, context: Context, action: Optional[Action]
    ) -> Tuple[str, ...]:
        failed = []
        for constraint in self.constraints:
            if constraint.phase != phase:
                continue
            try:
                valid = constraint.predicate(context, action) is True
            except Exception:
                valid = False
            if not valid:
                failed.append(constraint.constraint_id)
        return tuple(failed)

    def _decision(
        self,
        status: Status,
        context: Context,
        *,
        pre: Tuple[str, ...],
        post: Optional[Dict[str, Tuple[str, ...]]] = None,
        missing: Tuple[str, ...] = (),
        active: Optional[Dict[str, float]] = None,
        parent: Optional[Concept] = None,
        child: Optional[Concept] = None,
        scored: Sequence[ScoredAction] = (),
        selected: Optional[ScoredAction] = None,
        runner_up: Optional[ScoredAction] = None,
        verification: Optional[Verification] = None,
        handoff: Optional[str] = None,
    ) -> Decision:
        allowed_context = set(self._required_context_keys(context))
        branded_action_ids = {
            item.action_id
            for item in scored
            if "harmony_tolerance" in item.contributions
        }
        allowed_context.update(
            rule.context_key
            for rule in self.manifest.harmony_rules
            if rule.action_id in branded_action_ids
        )
        context_used = {
            key: _trace_value(context.values[key])
            for key in sorted(allowed_context)
            if key in context.values
        }
        source_ids = tuple(
            sorted(
                set(
                    (parent.source_case_ids if parent else ())
                    + (child.source_case_ids if child else ())
                )
            )
        )
        failure_ids = tuple(
            sorted(
                {failure_id for item in scored for failure_id in item.failure_case_ids}
            )
        )
        gap = runner_up.energy - selected.energy if runner_up and selected else None
        if gap is not None and gap < 0:
            raise ValueError("score gap cannot be negative")
        post_values = dict(post or {})
        active_values = dict(active or {})
        contribution_values = {
            item.action_id: dict(item.contributions) for item in scored
        }
        energy_values = {item.action_id: item.energy for item in scored}
        trace = DecisionTrace(
            formula_version=self.manifest.formula_version,
            parameter_version=self.manifest.parameter_version,
            concept_package_version=self.concept_package_version,
            manifest_version=self.manifest.version,
            source_record_ids=source_ids,
            failure_case_ids=failure_ids,
            pre_constraint_result=pre,
            post_constraint_results_by_action=MappingProxyType(post_values),
            context_used=MappingProxyType(context_used),
            missing_context=missing,
            world_state_id=context.world_state_id,
            active_concepts_with_alpha=MappingProxyType(active_values),
            selected_parent_id=parent.concept_id if parent else None,
            selected_concept_id=child.concept_id if child else None,
            gate=(child.gate_key, child.gate_value)
            if child and child.gate_key
            else None,
            action_contributions=MappingProxyType(
                {
                    action_id: MappingProxyType(contributions)
                    for action_id, contributions in contribution_values.items()
                }
            ),
            action_energies=MappingProxyType(energy_values),
            selected_action_id=selected.action_id if selected else None,
            selected_action_energy=selected.energy if selected else None,
            runner_up_id=runner_up.action_id if runner_up else None,
            runner_up_energy=runner_up.energy if runner_up else None,
            score_gap=gap,
            v_path=verification.v_path if verification else None,
            uncertainty=self.actions[selected.action_id].uncertainty
            if selected
            else None,
            handoff_reason=handoff,
            trace_digest="",
        )
        signed_trace = replace(trace, trace_digest=digest_decision_trace(trace))
        return Decision(status, selected.action_id if selected else None, signed_trace)


LCBCMEngine = LCBMEngine
