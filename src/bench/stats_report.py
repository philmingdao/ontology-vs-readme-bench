"""Statistical analyzer and Markdown/JSON report generator for ontology-vs-readme-bench."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from bench.tools import ARMS


def wilson_ci(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float, float]:
    """Return (rate, low, high) 95% Wilson score interval."""
    if total <= 0:
        return (0.0, 0.0, 0.0)
    p = successes / total
    denom = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * total)) / total)) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


def exact_mcnemar(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    """Exact two-sided binomial McNemar test on matched binary outcomes (a_pass, b_pass)."""
    b = sum(1 for a, c in pairs if a and not c)  # A pass, B fail
    c = sum(1 for a, d in pairs if not a and d)  # A fail, B pass
    n_disc = b + c
    if n_disc == 0:
        p_val = 1.0
    else:
        k = min(b, c)
        tail = sum(math.comb(n_disc, i) * (0.5 ** n_disc) for i in range(0, k + 1))
        p_val = min(1.0, 2.0 * tail)
    return {
        "n_pairs": len(pairs),
        "a_only_pass": b,
        "b_only_pass": c,
        "discordant": n_disc,
        "p_value": round(p_val, 6),
        "significant_05": p_val < 0.05,
    }


def _student_t_cdf(t_val: float, df: int) -> float:
    """Numerical CDF of Student's t distribution using composite Simpson's rule."""
    if df <= 0:
        return 0.5
    if t_val == 0.0:
        return 0.5
    sign = 1.0 if t_val > 0 else -1.0
    x = abs(t_val)
    # Integrate from 0 to x
    coef = math.gamma((df + 1) / 2.0) / (math.sqrt(df * math.pi) * math.gamma(df / 2.0))
    n_steps = 400
    h = x / n_steps
    total = 0.0
    for i in range(n_steps + 1):
        u = i * h
        pdf = coef * ((1.0 + (u * u) / df) ** (-(df + 1) / 2.0))
        w = 1.0 if (i == 0 or i == n_steps) else (4.0 if i % 2 == 1 else 2.0)
        total += w * pdf
    area = (h / 3.0) * total
    return max(0.0, min(1.0, 0.5 + sign * area))


def _student_t_crit(df: int, prob: float = 0.95) -> float:
    """Inverse Student's t CDF via bisection (default 95th percentile -> 90% two-sided CI)."""
    lo, hi = 0.0, 50.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _student_t_cdf(mid, df) < prob:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def paired_tost(
    pairs: list[tuple[bool, bool]],
    margins: tuple[float, ...] = (0.05, 0.10),
) -> dict[str, Any]:
    """Paired Two One-Sided Tests (TOST) for equivalence and non-inferiority of (A - B)."""
    n = len(pairs)
    if n < 2:
        return {"n_pairs": n, "error": "Need at least 2 pairs for TOST."}

    diffs = [(1.0 if a else 0.0) - (1.0 if b else 0.0) for a, b in pairs]
    mean_d = sum(diffs) / n
    var_d = sum((d - mean_d) ** 2 for d in diffs) / (n - 1)
    se = math.sqrt(var_d / n)
    df = n - 1
    t_crit_90 = _student_t_crit(df, 0.95)  # 90% two-sided CI for TOST at alpha=0.05
    ci90_low = mean_d - t_crit_90 * se
    ci90_high = mean_d + t_crit_90 * se

    results_by_margin: dict[str, Any] = {}
    for delta in margins:
        key = f"delta_{int(round(delta * 100))}pct"
        if se == 0.0:
            p_lower = 0.0 if mean_d > -delta else 1.0
            p_upper = 0.0 if mean_d < delta else 1.0
        else:
            t_lower = (mean_d + delta) / se
            t_upper = (mean_d - delta) / se
            # H01: diff <= -delta -> reject if t_lower large positive (p = 1 - CDF(t_lower))
            p_lower = 1.0 - _student_t_cdf(t_lower, df)
            # H02: diff >= +delta -> reject if t_upper large negative (p = CDF(t_upper))
            p_upper = _student_t_cdf(t_upper, df)
        tost_p = max(p_lower, p_upper)
        results_by_margin[key] = {
            "delta": delta,
            "non_inferior": (p_lower < 0.05) if se > 0 else (mean_d >= -delta),
            "equivalent": (tost_p < 0.05) if se > 0 else (abs(mean_d) <= delta),
            "non_inferior_p": round(p_lower, 6),
            "tost_p": round(tost_p, 6),
        }

    return {
        "n_pairs": n,
        "mean_diff": round(mean_d, 4),
        "se": round(se, 4),
        "ci90_low": round(ci90_low, 4),
        "ci90_high": round(ci90_high, 4),
        "margins": results_by_margin,
    }


def _file_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "lines": 0, "bytes": 0, "est_tokens": 0}
    text = path.read_text(encoding="utf-8")
    return {
        "exists": True,
        "lines": len(text.splitlines()),
        "bytes": len(text.encode("utf-8")),
        "est_tokens": int(round(len(text) / 3.8)),
    }


def compute_engineering_roi(
    project_root: Path,
    owl_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    readme_m = _file_metrics(project_root / "semantics" / "enterprise_readme.md")
    ont_m = _file_metrics(project_root / "semantics" / "ontology.ttl")
    shapes_m = _file_metrics(project_root / "semantics" / "shapes.ttl")
    engine_m = _file_metrics(project_root / "src" / "bench" / "owl_engine.py")

    return {
        "arms": {
            "bare_rds": {
                "semantic_files": [],
                "authoring_lines": 0,
                "runtime_code_lines": 0,
                "semantic_bytes": 0,
                "semantic_est_tokens": 0,
            },
            "readme_md": {
                "semantic_files": ["semantics/enterprise_readme.md"],
                "authoring_lines": readme_m["lines"],
                "runtime_code_lines": 0,
                "semantic_bytes": readme_m["bytes"],
                "semantic_est_tokens": readme_m["est_tokens"],
            },
            "raw_owl": {
                "semantic_files": ["semantics/ontology.ttl"],
                "authoring_lines": ont_m["lines"],
                "runtime_code_lines": 0,
                "semantic_bytes": ont_m["bytes"],
                "semantic_est_tokens": ont_m["est_tokens"],
            },
            "owl_reasoner": {
                "semantic_files": ["semantics/ontology.ttl", "semantics/shapes.ttl"],
                "authoring_lines": ont_m["lines"] + shapes_m["lines"],
                "runtime_code_lines": engine_m["lines"],
                "semantic_bytes": ont_m["bytes"] + shapes_m["bytes"],
                "semantic_est_tokens": ont_m["est_tokens"] + shapes_m["est_tokens"],
            },
        },
        "owl_runtime": owl_stats or {},
    }


def load_traces(trace_paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for p in trace_paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def summarize_traces(
    records: list[dict[str, Any]],
    project_root: Path,
    input_price_per_1m: float = 0.15,
    output_price_per_1m: float = 0.60,
    owl_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_model[str(r.get("model", "unknown"))].append(r)

    models_summary: dict[str, Any] = {}
    for model_name, recs in by_model.items():
        arms_present = [a for a in ARMS if any(r["arm"] == a for r in recs)]
        overall_by_arm: dict[str, Any] = {}
        tier_by_arm: dict[str, dict[str, Any]] = {}
        tier4_detail: dict[str, Any] = {}

        for arm in arms_present:
            a_recs = [r for r in recs if r["arm"] == arm]
            n = len(a_recs)
            k = sum(1 for r in a_recs if r["passed"])
            rate, lo, hi = wilson_ci(k, n)
            avg_pt = sum(r.get("prompt_tokens", 0) for r in a_recs) / n
            avg_ct = sum(r.get("completion_tokens", 0) for r in a_recs) / n
            avg_tt = sum(r.get("total_tokens", 0) for r in a_recs) / n
            avg_turns = sum(r.get("turns", 0) for r in a_recs) / n
            avg_tools = sum(r.get("tool_calls", 0) for r in a_recs) / n
            avg_lat = sum(r.get("latency_ms", 0.0) for r in a_recs) / n
            est_cost = (
                sum(r.get("prompt_tokens", 0) for r in a_recs) / 1_000_000.0 * input_price_per_1m
                + sum(r.get("completion_tokens", 0) for r in a_recs) / 1_000_000.0 * output_price_per_1m
            )
            overall_by_arm[arm] = {
                "n": n,
                "passed": k,
                "accuracy": round(rate, 4),
                "ci95_low": round(lo, 4),
                "ci95_high": round(hi, 4),
                "avg_prompt_tokens": round(avg_pt, 1),
                "avg_completion_tokens": round(avg_ct, 1),
                "avg_total_tokens": round(avg_tt, 1),
                "avg_turns": round(avg_turns, 2),
                "avg_tool_calls": round(avg_tools, 2),
                "avg_latency_ms": round(avg_lat, 1),
                "total_est_cost_usd": round(est_cost, 4),
            }

            tier_by_arm[arm] = {}
            for tier in (1, 2, 3, 4):
                t_recs = [r for r in a_recs if r["tier"] == tier]
                if not t_recs:
                    continue
                tn = len(t_recs)
                tk = sum(1 for r in t_recs if r["passed"])
                tr, tlo, thi = wilson_ci(tk, tn)
                tier_by_arm[arm][f"T{tier}"] = {
                    "n": tn,
                    "passed": tk,
                    "accuracy": round(tr, 4),
                    "ci95_low": round(tlo, 4),
                    "ci95_high": round(thi, 4),
                }

            t4_recs = [r for r in a_recs if r["tier"] == 4]
            if t4_recs:
                dec_ok = sum(1 for r in t4_recs if r.get("decision_exact"))
                rules_ok = sum(1 for r in t4_recs if r.get("rules_exact"))
                avg_f1 = sum(float(r.get("rules_f1") or 0.0) for r in t4_recs) / len(t4_recs)
                tier4_detail[arm] = {
                    "n": len(t4_recs),
                    "decision_acc": round(dec_ok / len(t4_recs), 4),
                    "rules_exact_acc": round(rules_ok / len(t4_recs), 4),
                    "avg_rules_f1": round(avg_f1, 4),
                }

        # Paired tests over matched (task_id, run_index)
        idx_map: dict[tuple[str, str, int], bool] = {
            (r["arm"], r["task_id"], int(r.get("run_index", 0))): bool(r["passed"]) for r in recs
        }
        key_pairs = [
            ("readme_md", "bare_rds"),
            ("readme_md", "raw_owl"),
            ("readme_md", "owl_reasoner"),
            ("owl_reasoner", "raw_owl"),
        ]
        mcnemar_results: dict[str, Any] = {}
        tost_results: dict[str, Any] = {}
        for a, b in key_pairs:
            if a not in arms_present or b not in arms_present:
                continue
            matched = []
            for (arm_k, tid, ridx), a_val in idx_map.items():
                if arm_k == a and (b, tid, ridx) in idx_map:
                    matched.append((a_val, idx_map[(b, tid, ridx)]))
            pair_name = f"{a}_vs_{b}"
            mcnemar_results[pair_name] = exact_mcnemar(matched)
            tost_results[pair_name] = paired_tost(matched)

        # Task matrix
        task_ids = sorted({r["task_id"] for r in recs})
        task_matrix: list[dict[str, Any]] = []
        for tid in task_ids:
            row: dict[str, Any] = {
                "task_id": tid,
                "tier": next(r["tier"] for r in recs if r["task_id"] == tid),
            }
            for arm in arms_present:
                sub = [r for r in recs if r["task_id"] == tid and r["arm"] == arm]
                row[arm] = {
                    "passed": sum(1 for r in sub if r["passed"]),
                    "total": len(sub),
                }
            task_matrix.append(row)

        models_summary[model_name] = {
            "is_mock": any(bool(r.get("mock")) for r in recs),
            "arms": arms_present,
            "overall_by_arm": overall_by_arm,
            "tier_by_arm": tier_by_arm,
            "tier4_detail": tier4_detail,
            "mcnemar": mcnemar_results,
            "tost": tost_results,
            "task_matrix": task_matrix,
        }

    return {
        "engineering_roi": compute_engineering_roi(project_root, owl_stats=owl_stats),
        "pricing": {
            "input_price_per_1m_usd": input_price_per_1m,
            "output_price_per_1m_usd": output_price_per_1m,
        },
        "models": models_summary,
    }


def render_markdown_report(summary: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Ontology (OWL 2 RL + SHACL) vs Natural Language README vs Bare RDS — 实验评测报告\n"
    ]

    roi = summary["engineering_roi"]
    lines.append("## 1. 知识工程建设与维护成本对比 (Engineering ROI)\n")
    lines.append("| 对照组 | 语义资产文件 | 语义规则行数 | 运行时推理代码行数 | 语义体积 (Bytes) | 估算语义 Token |")
    lines.append("|---|---|---|---|---|---|")
    for arm in ARMS:
        info = roi["arms"][arm]
        files_str = ", ".join(f"`{f}`" for f in info["semantic_files"]) or "无（仅 DDL + 样例）"
        lines.append(
            f"| `{arm}` | {files_str} | {info['authoring_lines']} | {info['runtime_code_lines']} "
            f"| {info['semantic_bytes']} | {info['semantic_est_tokens']} |"
        )
    if roi.get("owl_runtime"):
        rt = roi["owl_runtime"]
        lines.append(
            f"\n> **OWL 2 RL 推理机物理开销**: 原始三元组 `{rt.get('raw_triples', 'N/A')}` 条 → "
            f"Deductive Closure 后 `{rt.get('inferred_triples', 'N/A')}` 条 "
            f"(新增推理 `{rt.get('new_inferred_triples', 'N/A')}` 条)，初始化推理耗时 `{rt.get('reasoning_ms', 'N/A')} ms`。\n"
        )

    for model_name, mdata in summary["models"].items():
        mock_badge = " `[MOCK 离线模拟流水线结果 — 非真实 LLM 评测]`" if mdata["is_mock"] else ""
        lines.append(f"\n---\n\n## 2. 模型评测结果: `{model_name}`{mock_badge}\n")
        arms = mdata["arms"]

        # Overall table
        lines.append("### 2.1 总体准确率与 Token / 成本效率\n")
        lines.append("| 对照组 | 通过/总数 | 准确率 (95% Wilson CI) | 平均 Prompt Token | 平均总 Token | 平均轮次 | 平均延时 (ms) | 预估费用 (USD) |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for arm in arms:
            o = mdata["overall_by_arm"][arm]
            lines.append(
                f"| `{arm}` | {o['passed']}/{o['n']} | {o['accuracy']*100:.1f}% "
                f"([{o['ci95_low']*100:.1f}%, {o['ci95_high']*100:.1f}%]) | "
                f"{o['avg_prompt_tokens']:.0f} | {o['avg_total_tokens']:.0f} | "
                f"{o['avg_turns']:.2f} | {o['avg_latency_ms']:.1f} | \\${o['total_est_cost_usd']:.4f} |"
            )

        # Tier breakdown
        lines.append("\n### 2.2 四层梯度准确率拆解 (Tier 1–4)\n")
        header = "| Tier | " + " | ".join(f"`{a}`" for a in arms) + " |"
        sep = "|---|" + "|".join("---" for _ in arms) + "|"
        lines.extend([header, sep])
        tier_labels = {
            "T1": "Tier 1 (标准表层分析)",
            "T2": "Tier 2 (企业口径/扇出陷阱)",
            "T3": "Tier 3 (多层继承/传递闭包)",
            "T4": "Tier 4 (合规判定/动作拦截)",
        }
        for tk in ("T1", "T2", "T3", "T4"):
            cells = []
            for arm in arms:
                tinfo = mdata["tier_by_arm"].get(arm, {}).get(tk)
                if tinfo:
                    cells.append(f"{tinfo['accuracy']*100:.1f}% ({tinfo['passed']}/{tinfo['n']})")
                else:
                    cells.append("—")
            lines.append(f"| {tier_labels[tk]} | " + " | ".join(cells) + " |")

        # Tier 4 exact decision vs rule coverage
        if mdata["tier4_detail"]:
            lines.append("\n### 2.3 Tier 4 合规拦截细项 (Decision 准确率 vs 违规规则全集 F1)\n")
            lines.append("| 对照组 | Decision 准确率 | 违规规则集完全匹配率 | 平均 Rules F1 |")
            lines.append("|---|---|---|---|")
            for arm in arms:
                d4 = mdata["tier4_detail"].get(arm)
                if d4:
                    lines.append(
                        f"| `{arm}` | {d4['decision_acc']*100:.1f}% | "
                        f"{d4['rules_exact_acc']*100:.1f}% | {d4['avg_rules_f1']:.3f} |"
                    )

        # Statistical tests
        lines.append("\n### 2.4 统计检验: 配对 McNemar 检验 & TOST 等效/非劣效性检验\n")
        lines.append("| 对比组 (A vs B) | McNemar (A胜B负 / A负B胜) | McNemar exact p | 差值均值 (A-B) & 90% CI | ±5% 非劣效 / 等效 | ±10% 非劣效 / 等效 |")
        lines.append("|---|---|---|---|---|---|")
        for pair_name, mc in mdata["mcnemar"].items():
            to = mdata["tost"][pair_name]
            m5 = to["margins"]["delta_5pct"]
            m10 = to["margins"]["delta_10pct"]
            lines.append(
                f"| `{pair_name}` | {mc['a_only_pass']} / {mc['b_only_pass']} | "
                f"{mc['p_value']:.4f} ({'显著' if mc['significant_05'] else '无显著差异'}) | "
                f"{to['mean_diff']*100:+.1f}% ([{to['ci90_low']*100:+.1f}%, {to['ci90_high']*100:+.1f}%]) | "
                f"{'✓' if m5['non_inferior'] else '✗'} / {'✓' if m5['equivalent'] else '✗'} (p={m5['tost_p']:.3f}) | "
                f"{'✓' if m10['non_inferior'] else '✗'} / {'✓' if m10['equivalent'] else '✗'} (p={m10['tost_p']:.3f}) |"
            )

        # Task matrix
        lines.append("\n### 2.5 逐题通过矩阵 (`T1_01`–`T4_10`)\n")
        m_header = "| Task | Tier | " + " | ".join(f"`{a}`" for a in arms) + " |"
        m_sep = "|---|---|" + "|".join("---" for _ in arms) + "|"
        lines.extend([m_header, m_sep])
        for row in mdata["task_matrix"]:
            cells = []
            for arm in arms:
                st = row[arm]
                if st["total"] == 1:
                    cells.append("✓" if st["passed"] == 1 else "✗")
                else:
                    cells.append(f"{st['passed']}/{st['total']}")
            lines.append(f"| `{row['task_id']}` | T{row['tier']} | " + " | ".join(cells) + " |")

    return "\n".join(lines) + "\n"


def write_reports(
    trace_paths: list[Path],
    output_dir: Path,
    project_root: Path,
    input_price_per_1m: float = 0.15,
    output_price_per_1m: float = 0.60,
    owl_stats: dict[str, Any] | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records = load_traces(trace_paths)
    summary = summarize_traces(
        records=records,
        project_root=project_root,
        input_price_per_1m=input_price_per_1m,
        output_price_per_1m=output_price_per_1m,
        owl_stats=owl_stats,
    )
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(render_markdown_report(summary), encoding="utf-8")
    return summary_path, report_path, summary
