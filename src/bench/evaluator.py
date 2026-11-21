"""Deterministic execution & policy evaluator for benchmark submissions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from bench.tasks import TaskSpec


@dataclass
class EvalScore:
    passed: bool
    score: float
    reason: str
    decision_exact: bool | None = None
    rules_exact: bool | None = None
    rules_f1: float | None = None


def _parse_number(val: Any) -> float | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.strip().replace(",", "").replace("$", "").replace("%", "")
        if re.fullmatch(r"[-+]?\d+(\.\d+)?", cleaned):
            return float(cleaned)
    if isinstance(val, dict):
        if "value" in val:
            return _parse_number(val["value"])
        if "rows" in val and isinstance(val["rows"], list) and len(val["rows"]) == 1:
            row = val["rows"][0]
            if isinstance(row, list) and len(row) == 1:
                return _parse_number(row[0])
            if isinstance(row, dict) and len(row) == 1:
                return _parse_number(next(iter(row.values())))
        if len(val) == 1:
            return _parse_number(next(iter(val.values())))
    if isinstance(val, list) and len(val) == 1:
        return _parse_number(val[0])
    return None


def _parse_string(val: Any) -> str | None:
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, dict):
        if "value" in val:
            return _parse_string(val["value"])
        if "rows" in val and isinstance(val["rows"], list) and len(val["rows"]) == 1:
            row = val["rows"][0]
            if isinstance(row, list) and len(row) == 1:
                return _parse_string(row[0])
            if isinstance(row, dict) and len(row) == 1:
                return _parse_string(next(iter(row.values())))
        if len(val) == 1:
            return _parse_string(next(iter(val.values())))
    if isinstance(val, list) and len(val) == 1:
        return _parse_string(val[0])
    return str(val).strip() if val is not None else None


def _numbers_close(pred: float, exp: float) -> bool:
    tol = max(0.02, 0.001 * abs(exp))
    return abs(pred - exp) <= tol


def _normalize_cell(cell: Any) -> Any:
    if cell is None:
        return None
    if isinstance(cell, bool):
        return str(cell).lower()
    if isinstance(cell, (int, float)):
        return float(cell)
    if isinstance(cell, str):
        s = cell.strip()
        num = _parse_number(s) if re.fullmatch(r"[\$]?[-+]?[\d,]+(\.\d+)?%?", s) else None
        if num is not None:
            return num
        return s.lower()
    return str(cell).strip().lower()


def _cells_match(a: Any, b: Any) -> bool:
    na = _normalize_cell(a)
    nb = _normalize_cell(b)
    if isinstance(na, float) and isinstance(nb, float):
        return _numbers_close(na, nb)
    return na == nb


def _extract_table_rows(submitted: Any, expected_cols: tuple[str, ...]) -> list[list[Any]] | None:
    raw = submitted
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None

    if isinstance(raw, dict):
        if "rows" in raw and isinstance(raw["rows"], list):
            cols = raw.get("columns")
            rows = raw["rows"]
        elif "value" in raw:
            return _extract_table_rows(raw["value"], expected_cols)
        else:
            return None
    elif isinstance(raw, list):
        cols = None
        rows = raw
    else:
        return None

    normalized: list[list[Any]] = []
    for row in rows:
        if isinstance(row, list):
            normalized.append(row)
        elif isinstance(row, tuple):
            normalized.append(list(row))
        elif isinstance(row, dict):
            if expected_cols and all(c in row for c in expected_cols):
                normalized.append([row[c] for c in expected_cols])
            elif cols and all(c in row for c in cols):
                normalized.append([row[c] for c in cols])
            else:
                normalized.append(list(row.values()))
        else:
            normalized.append([row])
    return normalized


def _rows_match(pred_row: list[Any], gold_row: list[Any]) -> bool:
    if len(pred_row) != len(gold_row):
        return False
    return all(_cells_match(p, g) for p, g in zip(pred_row, gold_row))


def _canonical_rule_id(rule: str) -> str:
    s = rule.strip()
    m = re.match(r"^(R\d+)([a-zA-Z]?)$", s, re.IGNORECASE)
    if m:
        return f"R{int(m.group(1)[1:]):02d}{m.group(2).lower()}"
    return s.upper()


def _extract_decision_payload(submitted: Any) -> tuple[str, set[str]] | None:
    raw = submitted
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            m = re.search(r"\b(APPROVE|ESCALATE|REJECT)\b", raw, re.IGNORECASE)
            if not m:
                return None
            rules = set(re.findall(r"\bR\d{2}[ab]?\b", raw, re.IGNORECASE))
            return m.group(1).upper(), {_canonical_rule_id(r) for r in rules}

    if isinstance(raw, dict):
        if "value" in raw and isinstance(raw["value"], dict):
            raw = raw["value"]
        dec = str(raw.get("decision", "")).strip().upper()
        if dec not in ("APPROVE", "ESCALATE", "REJECT"):
            return None
        vr = raw.get("violated_rules", [])
        if isinstance(vr, str):
            vr = re.findall(r"\bR\d{2}[ab]?\b", vr, re.IGNORECASE)
        if not isinstance(vr, list):
            vr = []
        rules = {_canonical_rule_id(str(x)) for x in vr if str(x).strip()}
        return dec, rules
    return None


def evaluate_submission(task: TaskSpec, submitted: Any, gold: Any) -> EvalScore:
    if submitted is None:
        return EvalScore(passed=False, score=0.0, reason="No answer submitted.")

    if task.answer_type == "scalar_number":
        pred = _parse_number(submitted)
        exp = float(gold)
        if pred is None:
            return EvalScore(passed=False, score=0.0, reason=f"Could not parse numeric answer from: {submitted!r}")
        ok = _numbers_close(pred, exp)
        return EvalScore(
            passed=ok,
            score=1.0 if ok else 0.0,
            reason=f"pred={pred}, expected={exp}",
        )

    if task.answer_type == "scalar_string":
        pred = _parse_string(submitted)
        exp = str(gold).strip()
        if pred is None:
            return EvalScore(passed=False, score=0.0, reason=f"Could not parse string from: {submitted!r}")
        ok = pred.lower() == exp.lower()
        return EvalScore(
            passed=ok,
            score=1.0 if ok else 0.0,
            reason=f"pred={pred!r}, expected={exp!r}",
        )

    if task.answer_type == "table":
        pred_rows = _extract_table_rows(submitted, task.expected_columns)
        gold_rows: list[list[Any]] = gold
        if pred_rows is None:
            return EvalScore(passed=False, score=0.0, reason=f"Could not parse table rows from: {submitted!r}")
        if len(pred_rows) != len(gold_rows):
            return EvalScore(
                passed=False,
                score=0.0,
                reason=f"Row count mismatch: got {len(pred_rows)}, expected {len(gold_rows)}",
            )
        if task.ordered:
            ok = all(_rows_match(p, g) for p, g in zip(pred_rows, gold_rows))
            return EvalScore(
                passed=ok,
                score=1.0 if ok else 0.0,
                reason="Ordered row match" if ok else f"Ordered mismatch: pred={pred_rows}, expected={gold_rows}",
            )
        else:
            unmatched = list(gold_rows)
            for p in pred_rows:
                idx = next((i for i, g in enumerate(unmatched) if _rows_match(p, g)), None)
                if idx is None:
                    return EvalScore(
                        passed=False,
                        score=0.0,
                        reason=f"Unmatched row {p!r}; expected={gold_rows!r}",
                    )
                unmatched.pop(idx)
            return EvalScore(passed=True, score=1.0, reason="Unordered row match")

    if task.answer_type == "decision":
        parsed = _extract_decision_payload(submitted)
        if parsed is None:
            return EvalScore(
                passed=False,
                score=0.0,
                reason=f"Could not parse decision payload from: {submitted!r}",
                decision_exact=False,
                rules_exact=False,
                rules_f1=0.0,
            )
        pred_dec, pred_rules = parsed
        exp_dec = str(gold["decision"]).upper()
        exp_rules = {_canonical_rule_id(r) for r in gold["violated_rules"]}

        decision_exact = pred_dec == exp_dec
        rules_exact = pred_rules == exp_rules

        if not exp_rules and not pred_rules:
            rules_f1 = 1.0
        elif not exp_rules or not pred_rules:
            rules_f1 = 0.0
        else:
            inter = len(pred_rules & exp_rules)
            prec = inter / len(pred_rules)
            rec = inter / len(exp_rules)
            rules_f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

        passed = decision_exact and rules_exact
        return EvalScore(
            passed=passed,
            score=1.0 if passed else 0.0,
            reason=f"pred=({pred_dec}, {sorted(pred_rules)}), expected=({exp_dec}, {sorted(exp_rules)})",
            decision_exact=decision_exact,
            rules_exact=rules_exact,
            rules_f1=round(rules_f1, 4),
        )

    raise ValueError(f"Unsupported answer_type: {task.answer_type}")
