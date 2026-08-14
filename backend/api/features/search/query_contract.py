"""Validation rules for reproducible dashboard-search requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Iterator, Literal

from api.features.search.contracts import (
    SearchFilterUnsupported,
    SearchSyntaxUnsupported,
    SearchTimeFilterError,
)
from api.features.search.entities import resolve_entity_alias

_RELATIVE_TIME_RANGES = frozenset(
    {"", "不限", "近一天", "近一周", "近一月", "近三月", "近一年"}
)
_FIELD_SCOPE_RE = re.compile(
    r"(?:^|[\s(])(?:title|body|abstract|source|language|country|date|published_at):",
    re.IGNORECASE,
)
_PROXIMITY_RE = re.compile(r"(?:^|[\s(])NEAR(?:/\d+)?(?=\s|\)|$)", re.IGNORECASE)
_WILDCARD_RE = re.compile(r"[\w\u3400-\u9fff][*?]|[*?][\w\u3400-\u9fff]")
_REGEX_RE = re.compile(r"/(?:[^/\n]*[A-Za-z\u3400-\u9fff][^/\n]*)/")

QUERY_LANGUAGE_VERSION = "boolean-v1"
QUERY_LIMITS = MappingProxyType(
    {
        "max_query_chars": 512,
        "max_tokens": 64,
        "max_ast_nodes": 64,
        "max_terms": 32,
        "max_nesting_depth": 8,
        "max_phrase_chars": 160,
        "max_aliases_per_term": 10,
        "max_results_per_page": 100,
        "statement_timeout_seconds": 6,
        "max_title_candidates_per_positive_leaf": 1200,
    }
)


@dataclass(frozen=True)
class QueryNode:
    kind: Literal["term", "phrase", "and", "or", "not"]
    value: str = ""
    children: tuple["QueryNode", ...] = ()

    def as_dict(self) -> dict[str, Any]:
        if self.kind in {"term", "phrase"}:
            return {"type": self.kind, "value": self.value}
        if self.kind == "not":
            return {"type": "not", "operand": self.children[0].as_dict()}
        return {
            "type": self.kind,
            "children": [child.as_dict() for child in self.children],
        }


@dataclass(frozen=True)
class ParsedQuery:
    raw: str
    root: QueryNode
    token_count: int
    node_count: int
    term_count: int
    nesting_depth: int
    explicit_boolean: bool
    implicit_and: bool

    def limits_dict(self) -> dict[str, int]:
        return {
            **QUERY_LIMITS,
            "observed_query_chars": len(self.raw),
            "observed_tokens": self.token_count,
            "observed_ast_nodes": self.node_count,
            "observed_terms": self.term_count,
            "observed_nesting_depth": self.nesting_depth,
        }


@dataclass(frozen=True)
class _Token:
    kind: Literal["term", "phrase", "and", "or", "not", "lparen", "rparen"]
    value: str
    position: int


class QuerySyntaxIssue(ValueError):
    def __init__(self, feature: str, message: str, *, position: int | None = None):
        self.feature = feature
        self.position = position
        super().__init__(message)


def _static_unsupported_features(raw: str) -> tuple[str, ...]:
    unsupported: list[str] = []
    if "&&" in raw or "||" in raw:
        unsupported.append("symbolic_boolean_operator")
    if _PROXIMITY_RE.search(raw):
        unsupported.append("proximity_operator")
    if _FIELD_SCOPE_RE.search(raw) or "^" in raw:
        unsupported.append("field_scope_or_weight")
    if _WILDCARD_RE.search(raw):
        unsupported.append("wildcard")
    if _REGEX_RE.search(raw):
        unsupported.append("regular_expression")
    return tuple(dict.fromkeys(unsupported))


def _tokenize_query(raw: str) -> tuple[list[_Token], bool, int]:
    if len(raw) > QUERY_LIMITS["max_query_chars"]:
        raise QuerySyntaxIssue(
            "query_too_long",
            f"查询长度不得超过 {QUERY_LIMITS['max_query_chars']} 个字符",
        )
    for position, char in enumerate(raw):
        if ord(char) < 32 and not char.isspace():
            raise QuerySyntaxIssue(
                "control_character",
                "查询中不能包含控制字符",
                position=position,
            )
    tokens: list[_Token] = []
    explicit_boolean = False
    index = 0
    quote_closers = {'"': '"', "“": "”", "‘": "’"}
    while index < len(raw):
        char = raw[index]
        if char.isspace():
            index += 1
            continue
        if char == "(":
            tokens.append(_Token("lparen", char, index))
            explicit_boolean = True
            index += 1
            continue
        if char == ")":
            tokens.append(_Token("rparen", char, index))
            explicit_boolean = True
            index += 1
            continue
        if char in {"”", "’"}:
            raise QuerySyntaxIssue(
                "mixed_or_unbalanced_phrase",
                "查询中出现了没有对应开引号的闭引号",
                position=index,
            )

        # ASCII apostrophes inside names (for example People's) are ordinary
        # term characters. A leading ASCII apostrophe keeps the legacy quoted
        # phrase form, while double/curly quotes work anywhere in an expression.
        is_ascii_phrase = char == "'" and (
            index == 0 or raw[index - 1].isspace() or raw[index - 1] == "("
        )
        if char in quote_closers or is_ascii_phrase:
            closing = "'" if is_ascii_phrase else quote_closers[char]
            end = raw.find(closing, index + 1)
            if end < 0:
                raise QuerySyntaxIssue(
                    "mixed_or_unbalanced_phrase",
                    "引号短语缺少闭合引号",
                    position=index,
                )
            phrase = raw[index + 1 : end].strip()
            if not phrase:
                raise QuerySyntaxIssue(
                    "empty_phrase",
                    "引号短语不能为空",
                    position=index,
                )
            if any(ord(item) < 32 and not item.isspace() for item in phrase):
                raise QuerySyntaxIssue(
                    "control_character",
                    "引号短语中不能包含控制字符",
                    position=index,
                )
            if len(phrase) > QUERY_LIMITS["max_phrase_chars"]:
                raise QuerySyntaxIssue(
                    "phrase_too_long",
                    f"单个短语不得超过 {QUERY_LIMITS['max_phrase_chars']} 个字符",
                    position=index,
                )
            if "\\" in phrase:
                raise QuerySyntaxIssue(
                    "unsupported_escape_sequence",
                    "boolean-v1 不支持引号内转义序列",
                    position=index,
                )
            tokens.append(_Token("phrase", phrase, index))
            index = end + 1
            continue

        end = index
        while end < len(raw):
            current = raw[end]
            if current.isspace() or current in '()"“”‘’':
                break
            if current == "'" and (
                end == 0 or raw[end - 1].isspace() or raw[end - 1] == "("
            ):
                break
            end += 1
        value = raw[index:end]
        if not value:
            raise QuerySyntaxIssue(
                "invalid_token",
                "查询中包含无法识别的词元",
                position=index,
            )
        kind = value.lower()
        # Operators are deliberately uppercase. Lowercase natural-language
        # words such as "war or peace" remain searchable text.
        if value in {"AND", "OR", "NOT"}:
            tokens.append(_Token(kind, value, index))  # type: ignore[arg-type]
            explicit_boolean = True
        else:
            tokens.append(_Token("term", value, index))
        index = end
        if len(tokens) > QUERY_LIMITS["max_tokens"]:
            raise QuerySyntaxIssue(
                "too_many_tokens",
                f"查询词元不得超过 {QUERY_LIMITS['max_tokens']} 个",
                position=index,
            )
    return _merge_entity_tokens(tokens), explicit_boolean, len(tokens)


def _merge_entity_tokens(tokens: list[_Token]) -> list[_Token]:
    """Keep a curated multi-word entity alias as one logical term."""

    merged: list[_Token] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind != "term":
            merged.append(token)
            index += 1
            continue
        run_end = index
        while run_end < len(tokens) and tokens[run_end].kind == "term":
            run_end += 1
        cursor = index
        while cursor < run_end:
            chosen_end = cursor + 1
            chosen_value = tokens[cursor].value
            for end in range(min(run_end, cursor + 8), cursor + 1, -1):
                candidate = " ".join(item.value for item in tokens[cursor:end])
                if resolve_entity_alias(candidate) is not None:
                    chosen_end = end
                    chosen_value = candidate
                    break
            merged.append(_Token("term", chosen_value, tokens[cursor].position))
            cursor = chosen_end
        index = run_end
    return merged


class _QueryParser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.index = 0
        self.implicit_and = False
        self.max_depth = 0

    def parse(self) -> QueryNode:
        if not self.tokens:
            raise QuerySyntaxIssue("empty_query", "查询不能为空")
        root = self._parse_or(0)
        if self.index != len(self.tokens):
            token = self.tokens[self.index]
            raise QuerySyntaxIssue(
                "unexpected_token",
                f"无法在此处解析 {token.value!r}",
                position=token.position,
            )
        return root

    def _peek(self) -> _Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self) -> _Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    @staticmethod
    def _combine(kind: Literal["and", "or"], left: QueryNode, right: QueryNode) -> QueryNode:
        children: list[QueryNode] = []
        children.extend(left.children if left.kind == kind else (left,))
        children.extend(right.children if right.kind == kind else (right,))
        return QueryNode(kind, children=tuple(children))

    def _parse_or(self, depth: int) -> QueryNode:
        left = self._parse_and(depth)
        while self._peek() is not None and self._peek().kind == "or":
            self._take()
            right = self._parse_and(depth)
            left = self._combine("or", left, right)
        return left

    def _parse_and(self, depth: int) -> QueryNode:
        left = self._parse_not(depth)
        while True:
            token = self._peek()
            if token is None or token.kind in {"or", "rparen"}:
                return left
            if token.kind == "and":
                self._take()
            elif token.kind in {"term", "phrase", "not", "lparen"}:
                self.implicit_and = True
            else:
                raise QuerySyntaxIssue(
                    "unexpected_token",
                    f"无法在此处解析 {token.value!r}",
                    position=token.position,
                )
            right = self._parse_not(depth)
            left = self._combine("and", left, right)

    def _parse_not(self, depth: int) -> QueryNode:
        token = self._peek()
        if token is not None and token.kind == "not":
            self._take()
            return QueryNode("not", children=(self._parse_not(depth),))
        return self._parse_primary(depth)

    def _parse_primary(self, depth: int) -> QueryNode:
        token = self._peek()
        if token is None:
            raise QuerySyntaxIssue("missing_operand", "Boolean 运算符后缺少查询条件")
        if token.kind in {"term", "phrase"}:
            self._take()
            return QueryNode(token.kind, value=token.value)
        if token.kind == "lparen":
            if depth + 1 > QUERY_LIMITS["max_nesting_depth"]:
                raise QuerySyntaxIssue(
                    "nesting_too_deep",
                    f"括号嵌套不得超过 {QUERY_LIMITS['max_nesting_depth']} 层",
                    position=token.position,
                )
            self.max_depth = max(self.max_depth, depth + 1)
            self._take()
            if self._peek() is not None and self._peek().kind == "rparen":
                raise QuerySyntaxIssue(
                    "empty_group",
                    "括号组不能为空",
                    position=token.position,
                )
            node = self._parse_or(depth + 1)
            closing = self._peek()
            if closing is None or closing.kind != "rparen":
                raise QuerySyntaxIssue(
                    "unbalanced_parentheses",
                    "左括号缺少对应的右括号",
                    position=token.position,
                )
            self._take()
            return node
        raise QuerySyntaxIssue(
            "missing_operand",
            f"运算符 {token.value!r} 前缺少查询条件",
            position=token.position,
        )


def _node_stats(root: QueryNode) -> tuple[int, int]:
    nodes = 0
    terms = 0
    stack = [root]
    while stack:
        node = stack.pop()
        nodes += 1
        if node.kind in {"term", "phrase"}:
            terms += 1
        stack.extend(node.children)
    return nodes, terms


def _has_bounded_positive_anchor(node: QueryNode, *, positive: bool = True) -> bool:
    """Whether the expression can be generated from bounded positive hits.

    For positive AND, one bounded branch anchors the intersection; for positive
    OR, every branch needs an anchor. Negated operators apply De Morgan rules.
    """

    if node.kind in {"term", "phrase"}:
        return positive
    if node.kind == "not":
        return _has_bounded_positive_anchor(node.children[0], positive=not positive)
    checks = [
        _has_bounded_positive_anchor(child, positive=positive)
        for child in node.children
    ]
    if (node.kind == "and" and positive) or (node.kind == "or" and not positive):
        return any(checks)
    return all(checks)


def parse_supported_query(value: Any) -> ParsedQuery | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    unsupported = _static_unsupported_features(raw)
    if unsupported:
        raise QuerySyntaxIssue(
            unsupported[0],
            "boolean-v1 不支持该查询语法：" + ", ".join(unsupported),
        )
    tokens, explicit_boolean, lexical_token_count = _tokenize_query(raw)
    parser = _QueryParser(tokens)
    root = parser.parse()
    nodes, terms = _node_stats(root)
    if nodes > QUERY_LIMITS["max_ast_nodes"]:
        raise QuerySyntaxIssue(
            "too_many_ast_nodes",
            f"查询 AST 节点不得超过 {QUERY_LIMITS['max_ast_nodes']} 个",
        )
    if terms > QUERY_LIMITS["max_terms"]:
        raise QuerySyntaxIssue(
            "too_many_terms",
            f"查询条件不得超过 {QUERY_LIMITS['max_terms']} 个",
        )
    if not _has_bounded_positive_anchor(root):
        raise QuerySyntaxIssue(
            "unbounded_negation",
            "NOT 查询必须由每个 OR 分支中的正向词或短语限定，不能执行无界补集检索",
        )
    return ParsedQuery(
        raw=raw,
        root=root,
        token_count=lexical_token_count,
        node_count=nodes,
        term_count=terms,
        nesting_depth=parser.max_depth,
        explicit_boolean=explicit_boolean,
        implicit_and=parser.implicit_and,
    )


def iter_query_leaves(
    node: QueryNode,
    *,
    negated: bool = False,
) -> Iterator[tuple[QueryNode, bool]]:
    if node.kind in {"term", "phrase"}:
        yield node, negated
        return
    if node.kind == "not":
        yield from iter_query_leaves(node.children[0], negated=not negated)
        return
    for child in node.children:
        yield from iter_query_leaves(child, negated=negated)


def render_query_ast(node: QueryNode) -> str:
    if node.kind == "term":
        return node.value
    if node.kind == "phrase":
        return f'"{node.value}"'
    if node.kind == "not":
        return f"NOT ({render_query_ast(node.children[0])})"
    operator = f" {node.kind.upper()} "
    return "(" + operator.join(render_query_ast(child) for child in node.children) + ")"


def primary_query_text(params: Any) -> str:
    for field in ("keyword", "topic", "must_include"):
        value = str(getattr(params, field, None) or "").strip()
        if value:
            return value
    return ""


def is_explicit_phrase(value: str) -> bool:
    raw = str(value or "").strip()
    for opening, closing in (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’")):
        if raw.startswith(opening) and raw.endswith(closing) and len(raw) > 2:
            return bool(raw[len(opening) : -len(closing)].strip())
    return False


def detect_unsupported_syntax(value: str) -> tuple[str, ...]:
    raw = str(value or "").strip()
    if not raw:
        return ()
    unsupported = list(_static_unsupported_features(raw))
    if unsupported:
        return tuple(unsupported)
    try:
        parse_supported_query(raw)
    except QuerySyntaxIssue as exc:
        unsupported.append(exc.feature)
    return tuple(dict.fromkeys(unsupported))


def validate_supported_query(params: Any) -> None:
    keyword = str(getattr(params, "keyword", None) or "").strip()
    topic = str(getattr(params, "topic", None) or "").strip()
    if keyword and topic and keyword != topic:
        raise SearchSyntaxUnsupported(
            ("conflicting_primary_query_fields",),
            reason="keyword 与 topic 同时提供时必须完全一致，不能静默选择其中一个",
            query_field="keyword/topic",
        )
    unsupported: list[str] = []
    first_issue: QuerySyntaxIssue | None = None
    issue_field: str | None = None
    for field in ("keyword", "topic", "must_include", "any_include", "need_exclude"):
        value = getattr(params, field, None)
        try:
            parse_supported_query(value)
        except QuerySyntaxIssue as exc:
            unsupported.append(exc.feature)
            if first_issue is None:
                first_issue = exc
                issue_field = field
    if unsupported:
        raise SearchSyntaxUnsupported(
            tuple(dict.fromkeys(unsupported)),
            reason=str(first_issue) if first_issue is not None else None,
            position=first_issue.position if first_issue is not None else None,
            query_field=issue_field,
        )


def validate_clustered_vector_query(params: Any) -> None:
    """Reject structure the vector-only endpoint cannot execute faithfully."""

    for field in ("keyword", "topic", "must_include", "any_include", "need_exclude"):
        parsed = parse_supported_query(getattr(params, field, None))
        if parsed is None:
            continue
        has_phrase = any(
            node.kind == "phrase"
            for node, _negated in iter_query_leaves(parsed.root)
        )
        if parsed.explicit_boolean or has_phrase:
            raise SearchSyntaxUnsupported(
                ("clustered_vector_boolean_ast",),
                reason=(
                    "独立 clustered 向量端点不能忠实执行 Boolean/短语 AST；"
                    "请使用 /api/dashboard/search 的 mode=cluster"
                ),
                query_field=field,
            )


def _parse_datetime(value: Any, field: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("T", " "))
    except ValueError as exc:
        raise SearchTimeFilterError(
            f"{field} 必须是 ISO 8601 日期时间，例如 2026-08-09T12:30"
        ) from exc


def normalize_and_validate_time_semantics(params: Any, search_type: str) -> str:
    expected_field = "published_at" if search_type == "news" else "event_time"
    requested_field = str(getattr(params, "time_field", None) or "auto").strip()
    if getattr(params, "_requested_time_field", None) is None:
        params._requested_time_field = requested_field
    if requested_field == "auto":
        requested_field = expected_field
        params.time_field = expected_field
    if requested_field != expected_field:
        expected_label = (
            "published_at（新闻发布日期）"
            if search_type == "news"
            else "event_time（事件起止区间）"
        )
        raise SearchTimeFilterError(
            f"search_type={search_type} 仅支持 time_field={expected_label}；"
            "采集时间和更新时间尚不可筛选"
        )

    relative = str(getattr(params, "publish_time", None) or "").strip()
    if relative not in _RELATIVE_TIME_RANGES:
        raise SearchTimeFilterError(
            "publish_time 仅支持不限、近一天、近一周、近一月、近三月或近一年"
        )
    start = _parse_datetime(getattr(params, "start_time", None), "start_time")
    end = _parse_datetime(getattr(params, "end_time", None), "end_time")
    if start is not None and end is not None:
        try:
            invalid_order = start > end
        except TypeError as exc:
            raise SearchTimeFilterError(
                "start_time 与 end_time 必须使用一致的时区格式"
            ) from exc
        if invalid_order:
            raise SearchTimeFilterError("start_time 不得晚于 end_time")
    return expected_field


def validate_supported_filters(params: Any, search_type: str) -> None:
    unsupported: list[str] = []
    if str(getattr(params, "site", None) or "").strip():
        unsupported.append("site")
    sort_by = str(getattr(params, "sort_by", None) or "").strip()
    sort_order = str(getattr(params, "sort_order", None) or "desc").strip().lower()
    if search_type == "news":
        if str(getattr(params, "country", None) or "").strip():
            # A generic country value cannot identify source, audience, event,
            # or mentioned-country semantics. Fail closed until an explicit
            # dimension is implemented end to end.
            unsupported.append("country")
        if sort_by not in ("", "similarity", "pub_time", "published_at"):
            unsupported.append("sort_by")
        if sort_order not in ("asc", "desc"):
            unsupported.append("sort_order")
        elif sort_by in ("", "similarity") and sort_order != "desc":
            # Similarity/title-match mode has a descending publication-time
            # tiebreaker; accepting ascending would promise a control that is
            # not implemented by that ranking method.
            unsupported.append("sort_order")
    else:
        for field in ("data_source", "language", "country"):
            if str(getattr(params, field, None) or "").strip():
                unsupported.append(field)
        hit_location = str(getattr(params, "hit_location", None) or "全文").strip()
        if hit_location not in ("", "全文"):
            unsupported.append("hit_location")
        # L1/L2/L3 currently use their own disclosed system ordering. Accepting
        # a caller-selected "similarity" field would falsely imply a common
        # vector/relevance score across hierarchy levels.
        if sort_by:
            unsupported.append("sort_by")
        if sort_order != "desc":
            unsupported.append("sort_order")
    if unsupported:
        raise SearchFilterUnsupported(search_type, tuple(unsupported))


__all__ = (
    "ParsedQuery",
    "QUERY_LANGUAGE_VERSION",
    "QUERY_LIMITS",
    "QueryNode",
    "QuerySyntaxIssue",
    "detect_unsupported_syntax",
    "is_explicit_phrase",
    "iter_query_leaves",
    "normalize_and_validate_time_semantics",
    "parse_supported_query",
    "primary_query_text",
    "render_query_ast",
    "validate_supported_filters",
    "validate_clustered_vector_query",
    "validate_supported_query",
)
