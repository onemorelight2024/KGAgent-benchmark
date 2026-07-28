from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TypeVar

from allen import relation_allen_time_range

from tkgqg_shared import DELETED_RELATIONS, effective_time_range  # noqa: E402

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
#  Core data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    fid: int
    s: str
    r: str
    o: str
    ts: int
    te: int

    @property
    def time_type(self) -> str:
        """返回事件的语义时间类型。

        这里故意不直接用 ``ts == te`` 判断点事件，而是依赖 relation
        的人工分类结果。这样可以保证同一 relation 的时间解释在整条
        pipeline 里一致，并与 ``effective_time_range()`` 的 Allen 计算逻辑
        保持对齐。
        """
        from tkgqg_shared import RELATION_TEMPORAL_TYPE
        return "point" if RELATION_TEMPORAL_TYPE.get(self.r) == "P" else "interval"


@dataclass(frozen=True)
class Seed:
    seed_id: str
    seed_mode: str
    fixed_left: str
    fixed_relation: str
    fixed_right: str | None
    answer_role: str
    answer_ids: tuple[str, ...]


@dataclass(frozen=True)
class PathInstance:
    answer_id: str
    history_fids: tuple[int, ...]


@dataclass(frozen=True)
class StageSpec:
    stage_id: str
    stage_kind: str
    allowed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TemplateSpec:
    template_id: str
    template_name: str
    seed_mode: str
    hop_count: int
    graph_structure: str
    stages: tuple[StageSpec, ...]


@dataclass(frozen=True)
class ConstraintCandidate:
    candidate_id: str
    family: str
    subtype: str
    signature: tuple[str, ...]
    answer_ids: tuple[str, ...]
    support_fids: tuple[int, ...]
    payload: Mapping[str, object]


@dataclass(frozen=True)
class ForwardOption:
    relation: str
    next_answer_ids: tuple[str, ...]
    instances_by_answer: Mapping[str, tuple[PathInstance, ...]]
    support_fids: tuple[int, ...]


@dataclass
class TraceStep:
    step_index: int
    step_kind: str
    current_answer_role: str
    current_question_stub: str
    answer_ids: list[str]
    answer_count: int
    constraint_family: str | None
    constraint_payload: dict[str, object]
    support_fids: list[int]


@dataclass
class TraceState:
    seed: Seed
    template: TemplateSpec
    current_answer_role: str
    current_answer_ids: list[str]
    instances_by_answer: dict[str, list[PathInstance]]
    steps: list[TraceStep] = field(default_factory=list)
    participating_fids: set[int] = field(default_factory=set)
    temporal_constraint_count: int = 0
    backward_constraint_count: int = 0
    forward_transition_count: int = 0
    allen_codes: list[str] = field(default_factory=list)
    multi_event_temporal_types: list[str] = field(default_factory=list)
    used_multi_event_temporal: bool = False
    preferred_forward_relation: str | None = None
    preferred_backward_signature: tuple[str, str] | None = None


@dataclass
class BuildConfig:
    min_seed_answers: int
    max_seed_answers: int
    max_forward_answers: int
    max_instances_per_answer: int
    max_year: int
    attempts_per_seed_template: int
    random_seed: int
    max_per_template: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
#  Grouping accumulator — shared by all enumerate_*_candidates functions
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    """Accumulates answers, per-answer history paths, and support fids for one key."""

    answers: set[str] = field(default_factory=set)
    histories: defaultdict[str, set[tuple[int, ...]]] = field(
        default_factory=lambda: defaultdict(set),
    )
    support: set[int] = field(default_factory=set)


def _qualifying_histories(group: _Group) -> dict[str, tuple[tuple[int, ...], ...]]:
    """把 group 中累计的 history path 规整成稳定、可序列化的结构。

    候选约束在真正应用时，不只是要知道“哪个 answer 保留”，还要知道
    “这个 answer 的哪些 path 保留”。这里把内部 set 结构转成有序 tuple，
    方便后续写入 JSON、构造签名和做可重复比较。
    """
    return {aid: tuple(sorted(hists)) for aid, hists in group.histories.items()}


def _add_group_match(
    groups: defaultdict[object, _Group],
    key: object,
    answer_id: str,
    history_fids: tuple[int, ...],
    support_fids: Iterable[int],
) -> None:
    """把一次命中记录到指定分组里。

    ``enumerate_*_candidates`` 系列函数都会把“某个 answer 的某条 history
    满足某个约束键”的事实累加到 group 中。这里统一维护三类信息：
    1. 哪些 answer 命中了这个约束键
    2. 每个 answer 是哪些 history path 命中的
    3. 这些命中依赖了哪些 supporting event fid
    """
    group = groups[key]
    group.answers.add(answer_id)
    group.histories[answer_id].add(history_fids)
    group.support.update(support_fids)


def _iter_reducing_groups(
    groups: Mapping[object, _Group],
    current_count: int,
) -> Iterable[tuple[object, _Group]]:
    """只迭代那些能严格缩小当前答案集合的 group。

    约束候选必须满足“保留的答案数 > 0 且 < 当前答案总数”，否则：
    - 等于 0：约束不可执行，会把问题问死
    - 等于当前总数：约束没有区分力，不产生过滤效果
    这个函数把这条规则抽成公共过滤器。
    """
    for key, group in groups.items():
        if 0 < len(group.answers) < current_count:
            yield key, group


def _build_group_candidate(
    candidate_id: str,
    family: str,
    subtype: str,
    signature: tuple[str, ...],
    group: _Group,
    payload: Mapping[str, object],
) -> ConstraintCandidate:
    """把一个聚合后的 group 打包成统一的约束候选对象。

    ``ConstraintCandidate`` 是后续随机抽样、真正 apply、以及序列化 trace
    时使用的标准格式。这里除了拷贝 payload 之外，还会把 group 中累计的
    qualifying histories 一并塞进去，供 `_apply_constraint()` 精确裁剪 path。
    """
    return ConstraintCandidate(
        candidate_id=candidate_id,
        family=family,
        subtype=subtype,
        signature=signature,
        answer_ids=tuple(sorted(group.answers)),
        support_fids=tuple(sorted(group.support)),
        payload={
            **payload,
            "qualifying_histories": _qualifying_histories(group),
        },
    )


# ---------------------------------------------------------------------------
#  EventStore
# ---------------------------------------------------------------------------


class EventStore:
    def __init__(
        self,
        facts: list[Event],
        entity_map: dict[str, str],
        relation_map: dict[str, str],
        invalid_count: int,
        future_filtered_count: int,
    ) -> None:
        """构建内存事件索引。

        ``build_temporal_query_traces.py`` 的大多数操作都是围绕“按 subject、
        按 (subject, relation)、按 (relation, object) 取事件”展开的，因此
        这里一次性把常用倒排索引建好，后面候选枚举阶段就不需要反复扫全图。
        """
        self.facts = facts
        self.entity_map = entity_map
        self.relation_map = relation_map
        self.invalid_count = invalid_count
        self.future_filtered_count = future_filtered_count

        self.event_by_fid: dict[int, Event] = {f.fid: f for f in facts}
        self.events_by_sr: defaultdict[tuple[str, str], list[Event]] = defaultdict(list)
        self.events_by_ro: defaultdict[tuple[str, str], list[Event]] = defaultdict(list)
        self.events_by_s: defaultdict[str, list[Event]] = defaultdict(list)
        self.events_by_o: defaultdict[str, list[Event]] = defaultdict(list)

        for fact in facts:
            self.events_by_sr[(fact.s, fact.r)].append(fact)
            self.events_by_ro[(fact.r, fact.o)].append(fact)
            self.events_by_s[fact.s].append(fact)
            self.events_by_o[fact.o].append(fact)

    @classmethod
    def from_files(
        cls,
        kg_path: Path,
        entity_map_path: Path,
        relation_map_path: Path,
        max_year: int,
        ts_len: int = 0,
    ) -> EventStore:
        """从磁盘文件读取 KG，并完成基础清洗。

        这里会过滤四类事实：
        - ``ts > te`` 的非法事实
        - 超过 ``max_year`` 的未来事实
        - 在 ``DELETED_RELATIONS`` 中被显式剔除的 relation
        - 时间戳位数不匹配 ``ts_len``（0 = 不过滤；4 = year；6 = month；8 = day）

        返回的是一个已经建好索引的 ``EventStore``，后续所有 seed 搜索、
        时间约束枚举、forward/backward 扩展都会基于它运行。
        """
        entity_map = _load_text_map(entity_map_path)
        relation_map = _load_text_map(relation_map_path)
        facts: list[Event] = []
        invalid_count = 0
        future_filtered_count = 0
        deleted_relation_count = 0
        granularity_filtered_count = 0

        with kg_path.open() as f:
            for fid, line in enumerate(f):
                s, r, o, ts_raw, te_raw = line.rstrip("\n").split("\t")
                ts, te = int(ts_raw), int(te_raw)
                if ts > te:
                    invalid_count += 1
                    continue
                if ts_len and (len(ts_raw) != ts_len or len(te_raw) != ts_len):
                    granularity_filtered_count += 1
                    continue
                if ts > max_year or te > max_year:
                    future_filtered_count += 1
                    continue
                if r in DELETED_RELATIONS:
                    deleted_relation_count += 1
                    continue
                facts.append(Event(fid=fid, s=s, r=r, o=o, ts=ts, te=te))

        if granularity_filtered_count:
            print(f"  [filter] excluded {granularity_filtered_count} non-{ts_len}-digit facts (granularity mismatch)")
        if deleted_relation_count:
            print(f"  [filter] excluded {deleted_relation_count} facts from {len(DELETED_RELATIONS)} deleted relations")
        return cls(facts, entity_map, relation_map, invalid_count, future_filtered_count)

    def entity_text(self, qid: str) -> str:
        """把实体 ID 映射成人类可读文本；缺失时退回原 ID。"""
        return self.entity_map.get(qid, qid)

    def relation_text(self, pid: str) -> str:
        """把 relation ID 映射成人类可读文本；缺失时退回原 ID。"""
        return self.relation_map.get(pid, pid)

    def event_text(self, event: Event) -> str:
        """把一条事件格式化成便于阅读的单行字符串。"""
        time = str(event.ts) if event.ts == event.te else f"{event.ts} -> {event.te}"
        return (
            f"{self.entity_text(event.s)} --{self.relation_text(event.r)}--> "
            f"{self.entity_text(event.o)} [{time}]"
        )


# ---------------------------------------------------------------------------
#  Small helpers
# ---------------------------------------------------------------------------


def _load_text_map(path: Path) -> dict[str, str]:
    """读取 ``id<TAB>text`` 格式的映射文件。"""
    mapping: dict[str, str] = {}
    with path.open() as f:
        for line in f:
            key, value = line.rstrip("\n").split("\t", 1)
            mapping[key] = value
    return mapping


def _allen_code(left: Event, right: Event) -> str | None:
    """计算两条事件之间的 Allen 关系代码。

    注意这里不是直接把原始 ``(ts, te)`` 送进 Allen 计算，而是先用
    ``effective_time_range()`` 做 relation-aware 的时间解释。这样 point/
    interval 的判定由 relation 语义决定，而不是由原始时间戳形状偶然决定。

    如果两个时间范围不合法，底层可能抛 ``ValueError``，这里统一转成
    ``None``，表示“这对事件无法形成有效的 Allen 候选”。
    """
    try:
        left_range = effective_time_range(left.r, left.ts, left.te)
        right_range = effective_time_range(right.r, right.ts, right.te)
        return relation_allen_time_range(list(left_range), list(right_range))["code"]
    except ValueError:
        return None


def _event_sort_key(event: Event) -> tuple[int, int, int]:
    """给事件提供稳定排序键：先开始时间，再结束时间，最后用 fid 打破平局。"""
    return (event.ts, event.te, event.fid)


def _unique_objects(events: Sequence[Event]) -> list[str]:
    """从一批事件里取唯一 object ID，并按字典序返回。"""
    return sorted({e.o for e in events})


def _unique_subjects(events: Sequence[Event]) -> list[str]:
    """从一批事件里取唯一 subject ID，并按字典序返回。"""
    return sorted({e.s for e in events})


def _dedupe_instances(instances: Iterable[PathInstance], max_keep: int) -> list[PathInstance]:
    """按 history path 去重，并截断保留的实例数。

    同一个 answer 可能会通过多种等价路径反复出现，这里先按
    ``history_fids`` 去重，再优先保留更短、更稳定的路径，避免 trace 状态
    无限制膨胀。
    """
    dedup: dict[tuple[int, ...], PathInstance] = {}
    for inst in instances:
        dedup[inst.history_fids] = inst
    ranked = sorted(dedup.values(), key=lambda x: (len(x.history_fids), x.history_fids))
    return ranked[:max_keep]


def _frontier_event(store: EventStore, instance: PathInstance) -> Event:
    """取一条 path 当前“最前沿”的那条事件。

    ``history_fids`` 表示从 seed 到当前答案的一串事件链，最后一个 fid 就是
    当前层正在参与时间比较、forward 扩展或 backward 扩展的 frontier event。
    """
    return store.event_by_fid[instance.history_fids[-1]]


def _random_pick(items: Sequence[_T], rng: random.Random) -> _T | None:
    """从候选序列里随机选一个元素；空序列时返回 ``None``。"""
    return rng.choice(list(items)) if items else None


_MULTI_EVENT_WEIGHT = 0.1


def _pick_candidate(
    candidates: Sequence[ConstraintCandidate],
    rng: random.Random,
) -> ConstraintCandidate | None:
    """从约束候选列表里按权重随机选一个。

    ``temporal_multi_event`` 候选权重降为普通 Allen 候选的 ``_MULTI_EVENT_WEIGHT`` 倍，
    使 Allen 约束在两者都可用时被优先选中；multi-event 退化为保底。
    """
    if not candidates:
        return None
    weights = [
        _MULTI_EVENT_WEIGHT if c.family == "temporal_multi_event" else 1.0
        for c in candidates
    ]
    return rng.choices(list(candidates), weights=weights, k=1)[0]


def _serialize_event(event: Event, store: EventStore) -> dict[str, object]:
    """把事件对象转成可写入 JSON 的字典。

    除了原始字段，还补充了 ``time_type`` 以及实体/关系的可读文本，方便后续
    样本展示、报告生成和 benchmark materialization。
    """
    return {
        "fid": event.fid,
        "s": event.s,
        "r": event.r,
        "o": event.o,
        "ts": event.ts,
        "te": event.te,
        "time_type": event.time_type,
        "s_text": store.entity_text(event.s),
        "r_text": store.relation_text(event.r),
        "o_text": store.entity_text(event.o),
    }


# ---------------------------------------------------------------------------
#  Question text helpers
# ---------------------------------------------------------------------------


def _base_question_zh(seed: Seed, store: EventStore) -> str:
    """根据 seed 生成最原始的中文问题骨架。"""
    if seed.seed_mode == "sr":
        return f"{store.entity_text(seed.fixed_left)} 的「{store.relation_text(seed.fixed_relation)}」对象有哪些？"
    return f"谁满足「{store.relation_text(seed.fixed_relation)} -> {store.entity_text(seed.fixed_right or '')}」？"


def _step_question_stub(
    store: EventStore,
    state: TraceState,
    step_kind: str,
    payload: Mapping[str, object],
) -> str:
    """把当前 trace step 渲染成简短的中文问题片段。

    这个函数不追求最终自然语言质量，而是给调试、报告和中间产物提供一个
    “这一轮究竟加了什么条件”的可读摘要。
    """
    base = _base_question_zh(state.seed, store)
    if step_kind == "base":
        return base
    if step_kind == "temporal_filter":
        if "allen_code" in payload:
            return f"{base} 再加上一个时间条件：当前事件与相关事件满足 {payload['allen_code']}。"
        if "multi_event_type" in payload:
            return (
                f"{base} 再加上一个多事件排序条件：只保留"
                f"{payload['multi_event_type']} 排第 {payload['rank']} 的答案。"
            )
    if step_kind == "backward_filter":
        return (
            f"{base} 再加上一个 backward 条件：答案还必须满足"
            f"「{store.relation_text(str(payload['backward_relation']))}"
            f" -> {store.entity_text(str(payload['backward_object']))}」。"
        )
    if step_kind == "forward_transition":
        return (
            f"当前答案已经唯一，沿着「{store.relation_text(str(payload['forward_relation']))}」"
            "继续把问题推进到下一层。"
        )
    return base


def _record_step(
    store: EventStore,
    state: TraceState,
    step_kind: str,
    constraint_family: str | None,
    payload: Mapping[str, object],
    support_fids: Sequence[int],
) -> None:
    """把当前状态快照记录成一个 ``TraceStep``。

    每次做 base 初始化、应用时间约束、应用 backward 约束或 forward 跳转时，
    都会调用这里把“当时的问题形态、答案集合、证据事件、约束 payload”记下来，
    供后续序列化与分析使用。
    """
    state.steps.append(
        TraceStep(
            step_index=len(state.steps),
            step_kind=step_kind,
            current_answer_role=state.current_answer_role,
            current_question_stub=_step_question_stub(store, state, step_kind, payload),
            answer_ids=list(state.current_answer_ids),
            answer_count=len(state.current_answer_ids),
            constraint_family=constraint_family,
            constraint_payload=dict(payload),
            support_fids=sorted(set(support_fids)),
        )
    )


# ---------------------------------------------------------------------------
#  Seed discovery
# ---------------------------------------------------------------------------


def _find_sr_seeds(store: EventStore, config: BuildConfig) -> list[Seed]:
    """枚举 ``subject + relation`` 形式的 seed。

    对每个 ``(s, r)``，当前候选答案是所有不同的 object。只有当答案数落在
    ``[min_seed_answers, max_seed_answers]`` 范围内时，才把它当成一个可扩展的
    起点问题。
    """
    seeds: list[Seed] = []
    for (subject, relation), events in store.events_by_sr.items():
        answer_ids = _unique_objects(events)
        if config.min_seed_answers <= len(answer_ids) <= config.max_seed_answers:
            seeds.append(
                Seed(
                    seed_id=f"sr::{subject}::{relation}",
                    seed_mode="sr",
                    fixed_left=subject,
                    fixed_relation=relation,
                    fixed_right=None,
                    answer_role="object",
                    answer_ids=tuple(answer_ids),
                )
            )
    seeds.sort(key=lambda s: (len(s.answer_ids), s.seed_id))
    return seeds


def _find_ro_seeds(store: EventStore, config: BuildConfig) -> list[Seed]:
    """枚举 ``relation + object`` 形式的 seed。

    对每个 ``(r, o)``，当前候选答案是所有不同的 subject。这个方向主要服务于
    backward/ordinal 这类模板，因为它们更自然地从“谁满足某个目标对象条件”
    开始问。
    """
    seeds: list[Seed] = []
    for (relation, obj), events in store.events_by_ro.items():
        answer_ids = _unique_subjects(events)
        if config.min_seed_answers <= len(answer_ids) <= config.max_seed_answers:
            seeds.append(
                Seed(
                    seed_id=f"ro::{relation}::{obj}",
                    seed_mode="ro",
                    fixed_left=relation,
                    fixed_relation=relation,
                    fixed_right=obj,
                    answer_role="subject",
                    answer_ids=tuple(answer_ids),
                )
            )
    seeds.sort(key=lambda s: (len(s.answer_ids), s.seed_id))
    return seeds


# ---------------------------------------------------------------------------
#  Templates
# ---------------------------------------------------------------------------


def _build_default_templates() -> list[TemplateSpec]:
    """定义默认启用的图结构模板与阶段约束权限。

    模板本身只描述“这类问题要走几跳、分几轮、每轮允许出现哪些约束类型”，
    并不绑定具体实体或关系。真正运行时，会把模板与 seed 做组合，再由
    `_run_template_on_seed()` 驱动状态机执行。
    """
    S = StageSpec
    return [
        TemplateSpec(
            "sr_chain_hop2", "A->B->C", "sr", 2, "A->B->C",
            stages=(
                S("s0", "chain_entry", frozenset({"pairwise_allen", "forward_transition"})),
                S("s1", "post_forward", frozenset({"pairwise_allen", "multi_event_temporal"})),
            ),
        ),
        TemplateSpec(
            "sr_chain_hop3", "A->B->C->D", "sr", 3, "A->B->C->D",
            stages=(
                S("s0", "chain_entry", frozenset({"pairwise_allen", "forward_transition"})),
                S("s1", "post_forward", frozenset({"pairwise_allen", "forward_transition"})),
                S("s2", "post_forward", frozenset({"pairwise_allen", "multi_event_temporal"})),
            ),
        ),
        TemplateSpec(
            "ro_backward_fork", "C<-A->B", "ro", 2, "C<-A->B",
            stages=(
                S("s0", "backward_stage", frozenset({"pairwise_allen", "backward_filter"})),
            ),
        ),
        TemplateSpec(
            "ro_seed_self_allen", "single-family-self-allen", "ro", 1, "A->B",
            stages=(
                S("s0", "seed_self_allen", frozenset({"upward_allen"})),
            ),
        ),
    ]


# ---------------------------------------------------------------------------
#  State initialization
# ---------------------------------------------------------------------------


def _initialize_state(
    store: EventStore,
    seed: Seed,
    template: TemplateSpec,
    config: BuildConfig,
) -> TraceState:
    """把一个 seed 初始化成可运行的 trace state。

    初始化后的状态包含：
    - 当前答案集合 ``current_answer_ids``
    - 每个答案对应的 path 实例 ``instances_by_answer``
    - 已参与的 fid 集合 ``participating_fids``
    - 第一条 ``base`` step 记录

    也就是说，这一步把“静态 seed 定义”转换成了“后续可迭代过滤的动态状态”。
    """
    instances_by_answer: dict[str, list[PathInstance]] = defaultdict(list)
    if seed.seed_mode == "sr":
        events = store.events_by_sr[(seed.fixed_left, seed.fixed_relation)]
        for event in sorted(events, key=_event_sort_key):
            instances_by_answer[event.o].append(PathInstance(event.o, (event.fid,)))
    else:
        events = store.events_by_ro[(seed.fixed_relation, seed.fixed_right or "")]
        for event in sorted(events, key=_event_sort_key):
            instances_by_answer[event.s].append(PathInstance(event.s, (event.fid,)))

    normalized = {
        aid: _dedupe_instances(insts, config.max_instances_per_answer)
        for aid, insts in instances_by_answer.items()
    }

    state = TraceState(
        seed=seed,
        template=template,
        current_answer_role=seed.answer_role,
        current_answer_ids=sorted(normalized.keys()),
        instances_by_answer=normalized,
    )
    for insts in normalized.values():
        for inst in insts:
            state.participating_fids.update(inst.history_fids)
    _record_step(store, state, "base", None, {}, state.participating_fids)
    return state


# ---------------------------------------------------------------------------
#  Candidate enumeration
# ---------------------------------------------------------------------------


def _enumerate_multi_event_candidates(store: EventStore, state: TraceState) -> list[ConstraintCandidate]:
    """枚举多事件排序类时间约束。

    这类约束不比较两条事件之间的 Allen 关系，而是把当前答案各自对应的
    frontier event 取一个代表实例，然后按以下指标排序：
    - ``start_rank``: 开始时间排名
    - ``end_rank``: 结束时间排名
    - ``duration_rank``: 持续时长排名

    只有当某个排名位置是唯一的，才生成一个 singleton candidate；因此它往往
    会把当前答案集合直接收缩到 1。
    """
    if state.used_multi_event_temporal:
        return []

    representatives: list[tuple[str, PathInstance, Event]] = []
    for answer_id in state.current_answer_ids:
        instances = state.instances_by_answer.get(answer_id, [])
        if not instances:
            continue
        best = min(instances, key=lambda inst: _event_sort_key(_frontier_event(store, inst)))
        representatives.append((answer_id, best, _frontier_event(store, best)))

    if len(representatives) <= 1:
        return []

    candidates: list[ConstraintCandidate] = []
    metrics = [
        ("start_rank", lambda item: (item[2].ts, item[2].te, item[2].fid)),
        ("end_rank", lambda item: (item[2].te, item[2].ts, item[2].fid)),
        ("duration_rank", lambda item: (item[2].te - item[2].ts, item[2].ts, item[2].fid)),
    ]

    for metric_name, key_fn in metrics:
        ordered = sorted(representatives, key=key_fn)
        for index, item in enumerate(ordered, start=1):
            current_value = key_fn(item)
            prev_value = key_fn(ordered[index - 2]) if index > 1 else None
            next_value = key_fn(ordered[index]) if index < len(ordered) else None
            if current_value == prev_value or current_value == next_value:
                continue
            answer_id, instance, _ = item
            candidates.append(
                ConstraintCandidate(
                    candidate_id=f"multi::{metric_name}::{index}::{answer_id}",
                    family="temporal_multi_event",
                    subtype=metric_name,
                    signature=(metric_name, str(index), answer_id),
                    answer_ids=(answer_id,),
                    support_fids=instance.history_fids,
                    payload={
                        "multi_event_type": metric_name,
                        "rank": index,
                        "qualifying_histories": {answer_id: (instance.history_fids,)},
                    },
                )
            )
    return candidates


def _enumerate_chain_preview_candidates(store: EventStore, state: TraceState) -> list[ConstraintCandidate]:
    """为 chain 入口阶段枚举“预览下一跳”的 Allen 约束。

    当前层答案还是 seed 的直接候选，例如 ``A -> B1/B2/B3``。对每个候选 B：
    - ``prev`` 是已经在 path 里的 ``A->B`` 事件
    - ``next_evt`` 是从 B 再往外走的一条潜在 ``B->?`` 事件

    这里计算的是 ``prev`` 与 ``next_evt`` 的 Allen 关系，并按
    ``(next relation, allen code)`` 分组。这样得到的候选本质上在问：
    “哪些 B 能通过某条后续 relation 呈现出特定时间模式？”
    """
    groups: defaultdict[tuple[str, str], _Group] = defaultdict(_Group)

    for answer_id in state.current_answer_ids:
        for instance in state.instances_by_answer.get(answer_id, []):
            prev = _frontier_event(store, instance)
            for next_evt in store.events_by_s.get(answer_id, []):
                if next_evt.o == prev.s:
                    continue
                code = _allen_code(prev, next_evt)
                if code is None:
                    continue
                _add_group_match(
                    groups,
                    (next_evt.r, code),
                    answer_id,
                    instance.history_fids,
                    (prev.fid, next_evt.fid),
                )

    candidates: list[ConstraintCandidate] = []
    current_count = len(state.current_answer_ids)
    for (relation, allen_code), group in _iter_reducing_groups(groups, current_count):
        candidates.append(
            _build_group_candidate(
                candidate_id=f"chain::{relation}::{allen_code}",
                family="temporal_pairwise",
                subtype="allen_preview",
                signature=(relation, allen_code),
                group=group,
                payload={
                    "forward_relation": relation,
                    "allen_code": allen_code,
                },
            )
        )
    return candidates


def _enumerate_history_allen_candidates(store: EventStore, state: TraceState) -> list[ConstraintCandidate]:
    """在已经 forward 之后，枚举 path 末两条事件的 Allen 约束。

    对于 ``A->B->C`` 这类 path，history 的最后两条事件分别是 ``A->B`` 和
    ``B->C``。这里直接比较这两条事件的 Allen 关系，并按 relation code 分组，
    找出能缩小当前答案集合的 temporal candidates。
    """
    groups: defaultdict[str, _Group] = defaultdict(_Group)

    for answer_id in state.current_answer_ids:
        for instance in state.instances_by_answer.get(answer_id, []):
            if len(instance.history_fids) < 2:
                continue
            left = store.event_by_fid[instance.history_fids[-2]]
            right = store.event_by_fid[instance.history_fids[-1]]
            code = _allen_code(left, right)
            if code is None:
                continue
            _add_group_match(groups, code, answer_id, instance.history_fids, (left.fid, right.fid))

    candidates: list[ConstraintCandidate] = []
    current_count = len(state.current_answer_ids)
    for allen_code, group in _iter_reducing_groups(groups, current_count):
        candidates.append(
            _build_group_candidate(
                candidate_id=f"history::{allen_code}",
                family="temporal_pairwise",
                subtype="allen_history",
                signature=(allen_code,),
                group=group,
                payload={
                    "allen_code": allen_code,
                },
            )
        )
    return candidates


def _enumerate_backward_candidates(store: EventStore, state: TraceState) -> list[ConstraintCandidate]:
    """枚举结构型 backward 约束。

    这一步不做时间比较，只看“当前答案对应的 subject 是否还连接着某条共享分支
    ``(relation, object)``”。命中的答案会被按这个共享分支分组，形成
    ``structure_backward`` 候选，后续还可以在这个固定分支上继续做时间过滤。
    """
    groups: defaultdict[tuple[str, str], _Group] = defaultdict(_Group)

    for answer_id in state.current_answer_ids:
        for instance in state.instances_by_answer.get(answer_id, []):
            prev = _frontier_event(store, instance)
            for sibling in store.events_by_s.get(answer_id, []):
                if sibling.fid == prev.fid:
                    continue
                _add_group_match(
                    groups,
                    (sibling.r, sibling.o),
                    answer_id,
                    instance.history_fids,
                    (prev.fid, sibling.fid),
                )

    candidates: list[ConstraintCandidate] = []
    current_count = len(state.current_answer_ids)
    for (relation, obj), group in _iter_reducing_groups(groups, current_count):
        candidates.append(
            _build_group_candidate(
                candidate_id=f"backward::{relation}::{obj}",
                family="structure_backward",
                subtype="shared_subject_branch",
                signature=(relation, obj),
                group=group,
                payload={
                    "backward_relation": relation,
                    "backward_object": obj,
                },
            )
        )
    return candidates


def _enumerate_backward_temporal_candidates(store: EventStore, state: TraceState) -> list[ConstraintCandidate]:
    """在已经选定 backward 分支后，继续枚举该分支上的 Allen 约束。

    ``preferred_backward_signature`` 指向上一轮 backward filter 选中的
    ``(relation, object)``。这里固定这个 sibling 事件，再把它与当前 path
    的 frontier event 做 Allen 比较，形成更细粒度的时间约束候选。
    """
    if state.preferred_backward_signature is None:
        return []

    relation, obj = state.preferred_backward_signature
    groups: defaultdict[str, _Group] = defaultdict(_Group)

    for answer_id in state.current_answer_ids:
        for instance in state.instances_by_answer.get(answer_id, []):
            prev = _frontier_event(store, instance)
            for sibling in store.events_by_s.get(answer_id, []):
                if sibling.r != relation or sibling.o != obj:
                    continue
                code = _allen_code(prev, sibling)
                if code is None:
                    continue
                _add_group_match(groups, code, answer_id, instance.history_fids, (prev.fid, sibling.fid))

    candidates: list[ConstraintCandidate] = []
    current_count = len(state.current_answer_ids)
    for allen_code, group in _iter_reducing_groups(groups, current_count):
        candidates.append(
            _build_group_candidate(
                candidate_id=f"backward-temporal::{relation}::{obj}::{allen_code}",
                family="temporal_pairwise",
                subtype="allen_backward",
                signature=(relation, obj, allen_code),
                group=group,
                payload={
                    "backward_relation": relation,
                    "backward_object": obj,
                    "allen_code": allen_code,
                },
            )
        )
    return candidates


def _enumerate_upward_allen_candidates(store: EventStore, state: TraceState) -> list[ConstraintCandidate]:
    """枚举上行 Allen 约束：从候选池选一个实体作参照锚点，对其余候选计算 seed fact 间的 Allen 关系。

    与 pairwise_allen 系列"向下"比较（seed fact vs 同实体另一条 fact）不同，
    这里比较的是候选之间的 seed fact，例如：
    "谁的任期在奥巴马任期之后？" → Allen(候选.seed_fact, 奥巴马.seed_fact)

    锚点实体消耗出候选池（不进 answer_ids），进入 payload 作为约束参数。
    锚点的代表实例取 sort_key 最小的，与 multi_event 保持一致。
    """
    current_ids = state.current_answer_ids
    if len(current_ids) <= 1:
        return []

    candidates: list[ConstraintCandidate] = []
    current_count = len(current_ids)

    for ref_id in current_ids:
        ref_instances = state.instances_by_answer.get(ref_id, [])
        if not ref_instances:
            continue
        ref_best = min(ref_instances, key=lambda inst: _event_sort_key(_frontier_event(store, inst)))
        ref_event = _frontier_event(store, ref_best)

        groups: defaultdict[str, _Group] = defaultdict(_Group)

        for cand_id in current_ids:
            if cand_id == ref_id:
                continue
            for instance in state.instances_by_answer.get(cand_id, []):
                cand_event = _frontier_event(store, instance)
                code = _allen_code(cand_event, ref_event)
                if code is None:
                    continue
                _add_group_match(
                    groups,
                    code,
                    cand_id,
                    instance.history_fids,
                    (cand_event.fid, ref_event.fid),
                )

        # 锚点已移出候选池，group.answers ≤ current_count - 1 < current_count 恒成立，
        # 故 _iter_reducing_groups(groups, current_count) 的上界检查自动满足，
        # 实际只过滤空 group。
        for allen_code, group in _iter_reducing_groups(groups, current_count):
            candidates.append(
                _build_group_candidate(
                    candidate_id=f"upward::{ref_id}::{allen_code}",
                    family="temporal_upward",
                    subtype="allen_upward",
                    signature=(ref_id, allen_code),
                    group=group,
                    payload={
                        "allen_code": allen_code,
                        "reference_entity": ref_id,
                        "reference_entity_text": store.entity_text(ref_id),
                        "reference_fact_fid": ref_event.fid,
                    },
                )
            )

    return candidates


# ---------------------------------------------------------------------------
#  Constraint application
# ---------------------------------------------------------------------------


def _apply_constraint(
    store: EventStore,
    state: TraceState,
    candidate: ConstraintCandidate,
    config: BuildConfig,
) -> TraceState:
    """把一个候选约束真正应用到当前状态上。

    这一步会同时更新两层信息：
    1. 答案层：把 ``current_answer_ids`` 缩到候选保留的答案
    2. 路径层：只保留 payload 中声明的 qualifying histories

    然后根据候选类型更新统计量和“下一步偏好”：
    - backward 约束会设置 ``preferred_backward_signature``
    - Allen 约束可能设置 ``preferred_forward_relation``
    - multi-event 约束会标记 ``used_multi_event_temporal=True``
    最后把这次操作记录为一个 trace step。
    """
    new_instances: dict[str, list[PathInstance]] = {}
    qualifying = candidate.payload.get("qualifying_histories", {})
    if isinstance(qualifying, Mapping):
        for aid in candidate.answer_ids:
            accepted = set(qualifying.get(aid, ()))
            original = state.instances_by_answer.get(aid, [])
            filtered = [i for i in original if not accepted or i.history_fids in accepted]
            if filtered:
                new_instances[aid] = _dedupe_instances(filtered, config.max_instances_per_answer)
    else:
        for aid in candidate.answer_ids:
            original = state.instances_by_answer.get(aid, [])
            if original:
                new_instances[aid] = _dedupe_instances(original, config.max_instances_per_answer)

    state.current_answer_ids = list(candidate.answer_ids)
    state.instances_by_answer = new_instances
    state.participating_fids.update(candidate.support_fids)

    if candidate.family == "structure_backward":
        state.backward_constraint_count += 1
        state.preferred_backward_signature = (
            str(candidate.payload["backward_relation"]),
            str(candidate.payload["backward_object"]),
        )
        state.preferred_forward_relation = None
        _record_step(store, state, "backward_filter", candidate.family, candidate.payload, candidate.support_fids)
        return state

    state.temporal_constraint_count += 1
    if candidate.family == "temporal_multi_event":
        state.used_multi_event_temporal = True
        state.multi_event_temporal_types.append(str(candidate.payload["multi_event_type"]))
        state.preferred_forward_relation = None
    else:
        allen_code = candidate.payload.get("allen_code")
        if allen_code is not None:
            state.allen_codes.append(str(allen_code))
        if "forward_relation" in candidate.payload:
            state.preferred_forward_relation = str(candidate.payload["forward_relation"])
    _record_step(store, state, "temporal_filter", candidate.family, candidate.payload, candidate.support_fids)
    return state


# ---------------------------------------------------------------------------
#  Forward transitions
# ---------------------------------------------------------------------------


def _enumerate_forward_relations(
    store: EventStore,
    state: TraceState,
    config: BuildConfig,
    allow_single_answer: bool = False,
) -> list[ForwardOption]:
    """在当前答案已经唯一时，枚举所有合法的 forward 扩展。

    forward 的含义不是继续过滤当前变量，而是把问题推进到下一层变量。也就是：
    当前唯一答案作为新的中间节点，沿它的某条出边 relation 生成下一层答案集合。

    当 ``allow_single_answer=False``（默认）时，要求下一层至少 2 个候选；
    当 ``allow_single_answer=True`` 时（tc 已用完），允许只有 1 个候选——
    这样后续 stage 不需要任何约束就能完成，实现多跳但 tc=1 的 trace。
    """
    if len(state.current_answer_ids) != 1:
        return []

    current = state.current_answer_ids[0]
    allowed_rel = state.preferred_forward_relation
    grouped: defaultdict[str, defaultdict[str, list[PathInstance]]] = defaultdict(lambda: defaultdict(list))
    grouped_support: defaultdict[str, set[int]] = defaultdict(set)

    for instance in state.instances_by_answer.get(current, []):
        for next_evt in store.events_by_s.get(current, []):
            if allowed_rel is not None and next_evt.r != allowed_rel:
                continue
            new_inst = PathInstance(next_evt.o, instance.history_fids + (next_evt.fid,))
            grouped[next_evt.r][next_evt.o].append(new_inst)
            grouped_support[next_evt.r].update({*instance.history_fids, next_evt.fid})

    options: list[ForwardOption] = []
    for relation, answer_map in grouped.items():
        deduped: dict[str, tuple[PathInstance, ...]] = {}
        for aid, insts in answer_map.items():
            normalized = _dedupe_instances(insts, config.max_instances_per_answer)
            if normalized:
                deduped[aid] = tuple(normalized)
        next_answers = tuple(sorted(deduped.keys()))
        min_answers = 1 if allow_single_answer else 2
        if not (min_answers <= len(next_answers) <= config.max_forward_answers):
            continue
        options.append(
            ForwardOption(
                relation=relation,
                next_answer_ids=next_answers,
                instances_by_answer=deduped,
                support_fids=tuple(sorted(grouped_support[relation])),
            )
        )
    return options


def _apply_forward_transition(store: EventStore, state: TraceState, option: ForwardOption) -> TraceState:
    """应用一次 forward 跳转，把“当前答案”改成“下一层答案集合”。

    这一步会重置当前答案列表和实例路径，并清空上一轮的 forward/backward 偏好，
    因为进入新一层后，后续约束应该重新从新变量视角计算。
    """
    state.current_answer_role = "object"
    state.current_answer_ids = list(option.next_answer_ids)
    state.instances_by_answer = {aid: list(insts) for aid, insts in option.instances_by_answer.items()}
    state.participating_fids.update(option.support_fids)
    state.forward_transition_count += 1
    state.preferred_forward_relation = None
    state.preferred_backward_signature = None
    _record_step(
        store, state, "forward_transition", "forward_transition",
        {"forward_relation": option.relation}, option.support_fids,
    )
    return state


# ---------------------------------------------------------------------------
#  Main pipeline
# ---------------------------------------------------------------------------


def _enumerate_stage_candidates(
    store: EventStore,
    state: TraceState,
    stage: StageSpec,
) -> list[ConstraintCandidate]:
    """根据当前模板阶段，汇总这一轮允许使用的约束候选池。

    这是整条 pipeline 的“阶段调度器”。它不做真正过滤，只决定：
    - 当前 stage 允许调用哪类候选枚举器
    - 不同图结构下，应该从 chain / history / backward / ordinal 哪条逻辑分支取候选

    因此它回答的问题是：“如果现在要继续缩小答案，合法的下一步有哪些？”
    """
    pool: list[ConstraintCandidate] = []

    if "pairwise_allen" in stage.allowed:
        if stage.stage_kind == "chain_entry":
            pool.extend(_enumerate_chain_preview_candidates(store, state))
        elif stage.stage_kind == "post_forward":
            pool.extend(_enumerate_history_allen_candidates(store, state))
        elif (
            stage.stage_kind == "backward_stage"
            and state.preferred_backward_signature is not None
        ):
            pool.extend(_enumerate_backward_temporal_candidates(store, state))

    if (
        stage.stage_kind == "backward_stage"
        and state.preferred_backward_signature is None
        and "backward_filter" in stage.allowed
    ):
        pool.extend(_enumerate_backward_candidates(store, state))

    if "upward_allen" in stage.allowed:
        pool.extend(_enumerate_upward_allen_candidates(store, state))

    if "multi_event_temporal" in stage.allowed:
        pool.extend(_enumerate_multi_event_candidates(store, state))

    return pool


def _serialize_sample(store: EventStore, state: TraceState, template: TemplateSpec) -> dict[str, object]:
    """把最终完成的 trace state 转成样本 JSON 结构。

    输出会包含：
    - 样本级元信息
    - 完整步骤轨迹
    - 最终答案
    - 所有参与事件及其 fid
    - temporal/backward/forward 的统计量

    这是后续 benchmark 构建、报告展示和中间文件保存的统一中间表示。
    """
    answer_id = state.current_answer_ids[0]
    participating_fids = sorted(state.participating_fids)
    return {
        "sample_id": "",
        "template_id": template.template_id,
        "template_name": template.template_name,
        "hop_count": template.hop_count,
        "graph_structure": template.graph_structure,
        "seed": _serialize_seed(state.seed, store),
        "steps": [asdict(step) for step in state.steps],
        "answer": {"id": answer_id, "text": store.entity_text(answer_id)},
        "participating_fids": participating_fids,
        "participating_events": [
            _serialize_event(store.event_by_fid[fid], store)
            for fid in participating_fids
        ],
        "metadata": {
            "temporal_constraint_count": state.temporal_constraint_count,
            "backward_constraint_count": state.backward_constraint_count,
            "forward_transition_count": state.forward_transition_count,
            "allen_codes": list(state.allen_codes),
            "multi_event_temporal_types": list(state.multi_event_temporal_types),
            "seed_answer_count": len(state.seed.answer_ids),
            "answer_count": len(state.current_answer_ids),
        },
    }


def _run_template_on_seed(
    store: EventStore,
    seed: Seed,
    template: TemplateSpec,
    config: BuildConfig,
    rng: random.Random,
    force_tc_count: int | None = None,
) -> dict[str, object] | None:
    """在一个 seed 上实际执行某个模板，尝试生成一条完整 trace。

    执行逻辑是：
    1. 初始化 state
    2. 逐 stage 运行
    3. 每个 stage 内反复枚举候选并随机选一个 apply，直到当前答案唯一
    4. 如果当前 stage 允许 forward，则推进到下一层
    5. 所有 stage 完成后，要求最终答案唯一且至少使用过 1 个 temporal 约束

    当 ``force_tc_count`` 不为 None 时，强制 temporal 约束数精确等于该值，
    超出时提前放弃，不足时最终校验失败。
    """
    state = _initialize_state(store, seed, template, config)

    for stage in template.stages:
        while len(state.current_answer_ids) > 1:
            candidate = _pick_candidate(_enumerate_stage_candidates(store, state, stage), rng)
            if candidate is None:
                return None
            state = _apply_constraint(store, state, candidate, config)
            if force_tc_count is not None and state.temporal_constraint_count > force_tc_count:
                return None

        if "forward_transition" in stage.allowed:
            tc_exhausted = force_tc_count is not None and state.temporal_constraint_count >= force_tc_count
            option = _random_pick(
                _enumerate_forward_relations(store, state, config, allow_single_answer=tc_exhausted),
                rng,
            )
            if option is None:
                return None
            state = _apply_forward_transition(store, state, option)

    if len(state.current_answer_ids) != 1 or state.temporal_constraint_count == 0:
        return None
    if force_tc_count is not None and state.temporal_constraint_count != force_tc_count:
        return None
    return _serialize_sample(store, state, template)


# ---------------------------------------------------------------------------
#  Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_seed(seed: Seed, store: EventStore) -> dict[str, object]:
    """把 seed 转成便于下游消费的字典结构。"""
    common = {
        "seed_id": seed.seed_id,
        "seed_mode": seed.seed_mode,
        "fixed_relation": seed.fixed_relation,
        "fixed_relation_text": store.relation_text(seed.fixed_relation),
        "answer_role": seed.answer_role,
        "base_answer_count": len(seed.answer_ids),
    }
    if seed.seed_mode == "sr":
        common["fixed_subject"] = seed.fixed_left
        common["fixed_subject_text"] = store.entity_text(seed.fixed_left)
    else:
        common["fixed_object"] = seed.fixed_right
        common["fixed_object_text"] = store.entity_text(seed.fixed_right or "")
    return common


def _sample_signature(sample: Mapping[str, object]) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    """为样本生成去重签名。

    签名同时考虑：
    - 模板 ID
    - seed ID
    - 最终答案 ID
    - 每一步的 ``step_kind`` 与 ``constraint_payload``

    这样可以避免同一个 seed/template 因随机过程不同而重复产出语义完全相同的样本。
    """
    steps = sample["steps"]
    assert isinstance(steps, list)
    sig_steps = tuple(
        (step["step_kind"], json.dumps(step["constraint_payload"], ensure_ascii=False, sort_keys=True))
        for step in steps
    )
    final = sample["answer"]
    return (str(sample["template_id"]), str(sample["seed"]["seed_id"]), str(final["id"]), sig_steps)


# ---------------------------------------------------------------------------
#  Sample generation
# ---------------------------------------------------------------------------


def build_samples(store: EventStore, config: BuildConfig) -> list[dict[str, object]]:
    """批量运行所有 seed 与模板组合，生成最终样本列表。

    分两轮采样：
    - ``tc1`` 轮：强制 ``force_tc_count=1``，专门产出单约束 trace
    - ``any`` 轮：不限制约束数，产出混合 tc 的 trace
    两轮结果合并输出，通过 sample_id 中的 ``_tc1_`` / ``_any_`` 前缀区分。

    ``config.max_per_template`` 可限制每个模板在每轮最多产出多少条 trace，
    用于平衡各模板的比例（如限制 ro_seed_self_allen 避免 1-hop 过多）。
    """
    sr_seeds = _find_sr_seeds(store, config)
    ro_seeds = _find_ro_seeds(store, config)
    templates = _build_default_templates()
    rng = random.Random(config.random_seed)

    all_samples: list[dict[str, object]] = []

    for mode, force_tc in [("tc1", 1), ("any", None)]:
        samples: list[dict[str, object]] = []
        seen: set[tuple[str, str, str, tuple[tuple[str, str], ...]]] = set()
        template_counts: dict[str, int] = {}

        for template in templates:
            cap = config.max_per_template.get(template.template_id)
            seed_pool = sr_seeds if template.seed_mode == "sr" else ro_seeds
            for seed in seed_pool:
                if cap is not None and template_counts.get(template.template_id, 0) >= cap:
                    break
                for _ in range(config.attempts_per_seed_template):
                    sample = _run_template_on_seed(
                        store, seed, template, config, rng,
                        force_tc_count=force_tc,
                    )
                    if sample is None:
                        continue
                    sig = _sample_signature(sample)
                    if sig in seen:
                        continue
                    sample["sample_id"] = f"{template.template_id}_{mode}_{len(samples):06d}"
                    samples.append(sample)
                    seen.add(sig)
                    template_counts[template.template_id] = template_counts.get(template.template_id, 0) + 1
                    break

        print(f"  mode={mode}: {len(samples)} samples")
        for tmpl, cnt in sorted(template_counts.items()):
            print(f"    {tmpl}: {cnt}")
        all_samples.extend(samples)

    return all_samples


# ---------------------------------------------------------------------------
#  Report / manifest
# ---------------------------------------------------------------------------


def _build_manifest(store: EventStore, config: BuildConfig, samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """汇总样本分布与运行统计，生成 manifest。

    manifest 用来回答两类问题：
    - 数据规模：总样本数、各模板/图结构/hop 的数量
    - 约束分布：各类 step、Allen code、多事件类型的使用频次
    """
    by_template: defaultdict[str, int] = defaultdict(int)
    by_graph: defaultdict[str, int] = defaultdict(int)
    allen_counter: defaultdict[str, int] = defaultdict(int)
    multi_counter: defaultdict[str, int] = defaultdict(int)
    hop_counter: defaultdict[str, int] = defaultdict(int)
    step_counter: defaultdict[str, int] = defaultdict(int)

    for sample in samples:
        by_template[str(sample["template_id"])] += 1
        by_graph[str(sample["graph_structure"])] += 1
        hop_counter[str(sample["hop_count"])] += 1
        meta = sample["metadata"]
        for code in meta["allen_codes"]:
            allen_counter[str(code)] += 1
        for code in meta["multi_event_temporal_types"]:
            multi_counter[str(code)] += 1
        for step in sample["steps"]:
            step_counter[str(step["step_kind"])] += 1

    return {
        "config": asdict(config),
        "fact_stats": {
            "clean_facts": len(store.facts),
            "invalid_count": store.invalid_count,
            "future_filtered_count": store.future_filtered_count,
        },
        "sample_stats": {
            "total_samples": len(samples),
            "by_template": dict(sorted(by_template.items())),
            "by_graph_structure": dict(sorted(by_graph.items())),
            "by_hop_count": dict(sorted(hop_counter.items())),
            "step_kind_usage": dict(sorted(step_counter.items())),
            "allen_code_usage": dict(sorted(allen_counter.items())),
            "multi_event_usage": dict(sorted(multi_counter.items())),
        },
    }


def _format_event_time(event: Mapping[str, object]) -> str:
    """把事件时间格式化成报告里更紧凑的显示形式。"""
    return str(event["ts"]) if event["ts"] == event["te"] else f"{event['ts']} -> {event['te']}"


def _build_report_markdown(store: EventStore, samples: Sequence[Mapping[str, object]]) -> str:
    """把部分样本渲染成 Markdown 报告，便于人工浏览。

    报告不是完整导出，而是按模板分组，每组只展示少量代表样本，重点让人快速看到：
    - 图结构
    - 最终答案
    - 轨迹中每一步如何缩小答案
    - 参与了哪些事件
    """
    lines = ["# Temporal Query Trace Rewrite Report", "", "这份文件展示新框架下生成的样例。", ""]

    by_template: defaultdict[str, list[Mapping[str, object]]] = defaultdict(list)
    for sample in samples:
        by_template[str(sample["template_id"])].append(sample)

    for template_id, bucket in sorted(by_template.items()):
        lines.append(f"## {template_id}")
        lines.append("")
        for sample in bucket[:3]:
            meta = sample["metadata"]
            lines.append(f"### {sample['sample_id']}")
            lines.append("")
            lines.append(f"- 图结构：`{sample['graph_structure']}`")
            lines.append(f"- 最终答案：`{sample['answer']['text']}`")
            lines.append(f"- Seed：`{sample['seed']['seed_id']}`")
            lines.append(
                f"- 元数据：`temporal={meta['temporal_constraint_count']}`, "
                f"`backward={meta['backward_constraint_count']}`, "
                f"`forward={meta['forward_transition_count']}`"
            )
            lines.append("- 轨迹：")
            for step in sample["steps"]:
                lines.append(f"  - `{step['step_kind']}`: `{step['answer_count']}` 个答案，{step['current_question_stub']}")
            lines.append("- 参与事件：")
            for event in sample["participating_events"][:8]:
                lines.append(f"  - {event['s_text']} --{event['r_text']}--> {event['o_text']} [{_format_event_time(event)}]")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  I/O
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    """把一组字典逐行写成 JSONL 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    """把单个对象写成缩进格式的 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """定义命令行参数并返回解析结果。"""
    parser = argparse.ArgumentParser(description="Build temporal query trace samples with the rewritten pipeline.")
    parser.add_argument("--kg-path", type=Path, required=True,
                        help="Path to TKG file (TSV: subject\\trelation\\tobject\\tts\\tte)")
    parser.add_argument("--entity-map-path", type=Path, required=True,
                        help="Path to entity id→text mapping (TSV: id\\ttext)")
    parser.add_argument("--relation-map-path", type=Path, required=True,
                        help="Path to relation id→text mapping (TSV: id\\ttext)")
    parser.add_argument(
        "--output-jsonl", type=Path,
        default=Path("output") / "traces" / "trace_samples.jsonl",
    )
    parser.add_argument(
        "--manifest-json", type=Path,
        default=Path("output") / "traces" / "manifest.json",
    )
    parser.add_argument(
        "--report-md", type=Path,
        default=Path("output") / "traces" / "report_examples.md",
    )
    parser.add_argument("--min-seed-answers", type=int, default=5)
    parser.add_argument("--max-seed-answers", type=int, default=50)
    parser.add_argument("--max-forward-answers", type=int, default=50)
    parser.add_argument("--max-instances-per-answer", type=int, default=8)
    parser.add_argument("--max-year", type=int, default=2100)
    parser.add_argument("--ts-len", type=int, default=0,
                        help="Filter by timestamp digit length (0=none, 4=year, 6=month, 8=day)")
    parser.add_argument("--attempts-per-seed-template", type=int, default=4)
    parser.add_argument("--random-seed", type=int, default=13)
    parser.add_argument("--max-per-template", type=str, default=None,
                        help='JSON dict capping per-template output, e.g. \'{"ro_seed_self_allen": 5000}\'')
    return parser.parse_args()


def main() -> None:
    """脚本入口。

    负责串起完整流程：
    1. 读取命令行参数
    2. 构造 ``BuildConfig``
    3. 从 KG 文件加载 ``EventStore``
    4. 生成 trace samples
    5. 输出 JSONL、manifest 和 Markdown 报告
    """
    args = _parse_args()
    config = BuildConfig(
        min_seed_answers=args.min_seed_answers,
        max_seed_answers=args.max_seed_answers,
        max_forward_answers=args.max_forward_answers,
        max_instances_per_answer=args.max_instances_per_answer,
        max_year=args.max_year,
        attempts_per_seed_template=args.attempts_per_seed_template,
        random_seed=args.random_seed,
        max_per_template=(
            args.max_per_template if isinstance(args.max_per_template, dict)
            else (json.loads(args.max_per_template) if args.max_per_template else {})
        ),
    )
    store = EventStore.from_files(
        kg_path=args.kg_path,
        entity_map_path=args.entity_map_path,
        relation_map_path=args.relation_map_path,
        max_year=config.max_year,
        ts_len=args.ts_len,
    )
    samples = build_samples(store, config)
    manifest = _build_manifest(store, config, samples)
    report = _build_report_markdown(store, samples)

    _write_jsonl(args.output_jsonl, samples)
    _write_json(args.manifest_json, manifest)
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.write_text(report)

    print(f"samples written to: {args.output_jsonl}")
    print(f"manifest written to: {args.manifest_json}")
    print(f"report written to: {args.report_md}")
    print(f"total samples: {len(samples)}")


if __name__ == "__main__":
    main()
