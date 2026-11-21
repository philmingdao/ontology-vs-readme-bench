"""CLI entrypoint for ontology-vs-readme-bench."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

from bench.agent import MockAgentRunner, OpenAIAgentRunner, RunTrace
from bench.db_builder import build_database
from bench.owl_engine import EnterpriseOntologyEngine
from bench.stats_report import write_reports
from bench.tasks import TASKS, compute_ground_truth, get_tasks
from bench.tools import ARMS

ALL_CANONICAL_RULES = [
    f"R{i:02d}" for i in range(1, 23)
] + ["R23a", "R23b"] + [f"R{i:02d}" for i in range(24, 29)]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def cmd_build_db(args: argparse.Namespace) -> int:
    root = _project_root()
    db_path = Path(args.db_path) if args.db_path else root / "data" / "enterprise.db"
    counts = build_database(db_path=db_path, seed=args.seed)
    print(f"Built database at: {db_path}")
    for tbl, cnt in counts.items():
        print(f"  - {tbl}: {cnt} rows")
    return 0


def verify_all(root: Path, db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        build_database(db_path=db_path, seed=42)

    semantics_dir = root / "semantics"
    engine = EnterpriseOntologyEngine(
        db_path=db_path,
        ontology_path=semantics_dir / "ontology.ttl",
        shapes_path=semantics_dir / "shapes.ttl",
    )

    # 1. Verify all 40 tasks produce valid ground truths
    ground_truths: dict[str, Any] = {}
    for task in TASKS:
        gt = compute_ground_truth(db_path, task)
        if task.answer_type == "table" and len(gt) == 0:
            raise AssertionError(f"{task.task_id} returned empty table ground truth!")
        if task.answer_type == "scalar_number" and gt is None:
            raise AssertionError(f"{task.task_id} returned None numeric ground truth!")
        ground_truths[task.task_id] = gt

    # 2. Cross-check Tier 3 OWL inferred sets vs SQL recursive CTEs
    sanc_accts_sql = [r[0] for r in ground_truths["T3_04"]]
    sanc_accts_owl = [m["id"] for m in engine.list_classes_and_members("SanctionedAccount")["members"]]
    if sanc_accts_sql != sanc_accts_owl:
        raise AssertionError(f"T3_04 mismatch: SQL={sanc_accts_sql} vs OWL={sanc_accts_owl}")

    sanc_vndrs_sql = [r[0] for r in ground_truths["T3_05"]]
    sanc_vndrs_owl = [m["id"] for m in engine.list_classes_and_members("SanctionedVendor")["members"]]
    if sanc_vndrs_sql != sanc_vndrs_owl:
        raise AssertionError(f"T3_05 mismatch: SQL={sanc_vndrs_sql} vs OWL={sanc_vndrs_owl}")

    exp_reg_sql = [r[0] for r in ground_truths["T3_07"]]
    exp_reg_owl = [m["id"] for m in engine.list_classes_and_members("ExportRegulatedItem")["members"]]
    if exp_reg_sql != exp_reg_owl:
        raise AssertionError(f"T3_07 mismatch: SQL={exp_reg_sql} vs OWL={exp_reg_owl}")

    # Verify dual-use hardware SKUs are also inferred under RegulatedProduct in OWL
    reg_owl_ids = {m["id"] for m in engine.list_classes_and_members("RegulatedProduct")["members"]}
    for dual_id in ("SKU_HW_DUAL", "SKU_HW_DUAL2", "SKU_ACT_02", "SKU_RAD_DUAL"):
        if dual_id not in reg_owl_ids:
            raise AssertionError(f"OWL did not infer {dual_id} as RegulatedProduct!")

    # 3. Cross-check all 10 Tier 4 tasks: SQL policy verifier vs pyshacl over OWL 2 RL graph
    for task in [t for t in TASKS if t.tier == 4]:
        assert task.po_req_id is not None
        sql_gt = ground_truths[task.task_id]
        shacl_gt = engine.validate_po_shacl(task.po_req_id)
        if sql_gt["decision"] != shacl_gt["decision"] or sql_gt["violated_rules"] != shacl_gt["violated_rules"]:
            raise AssertionError(
                f"{task.task_id} ({task.po_req_id}) mismatch: SQL={sql_gt} vs SHACL={shacl_gt}"
            )

    # 4. Audit information equivalence rule coverage (R01..R28)
    readme_txt = (semantics_dir / "enterprise_readme.md").read_text(encoding="utf-8")
    owl_txt = (semantics_dir / "ontology.ttl").read_text(encoding="utf-8")
    shapes_txt = (semantics_dir / "shapes.ttl").read_text(encoding="utf-8")
    eq_txt = (root / "docs" / "information_equivalence.md").read_text(encoding="utf-8")

    for r in ALL_CANONICAL_RULES:
        pat = re.compile(rf"\b{re.escape(r)}\b")
        if not pat.search(readme_txt):
            raise AssertionError(f"Rule {r} missing from semantics/enterprise_readme.md")
        if not (pat.search(owl_txt) or pat.search(shapes_txt)):
            raise AssertionError(f"Rule {r} missing from semantics/ontology.ttl and shapes.ttl")
        if not pat.search(eq_txt):
            raise AssertionError(f"Rule {r} missing from docs/information_equivalence.md")

    return {
        "tasks_verified": len(TASKS),
        "owl_stats": engine.stats(),
        "rules_audited": len(ALL_CANONICAL_RULES),
        "sample_ground_truths": {
            "T2_01_bookings_usd": ground_truths["T2_01"],
            "T2_06_credit_breach": ground_truths["T2_06"],
            "T3_04_sanctioned_accounts": sanc_accts_owl,
            "T3_05_sanctioned_vendors": sanc_vndrs_owl,
            "T3_08_pending_sanc_po_usd": ground_truths["T3_08"],
            "T4_08_multi_violation": ground_truths["T4_08"],
        },
    }


def cmd_verify(args: argparse.Namespace) -> int:
    root = _project_root()
    db_path = Path(args.db_path) if args.db_path else root / "data" / "enterprise.db"
    res = verify_all(root=root, db_path=db_path)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    print("ALL 40 TASKS & OWL/SHACL CROSS-CHECKS PASSED")
    return 0


def _load_secrets_file(secrets_path: Path) -> tuple[str | None, str | None]:
    """Read provider:openai api_key and base_url strictly in memory without logging."""
    data = json.loads(secrets_path.read_text(encoding="utf-8"))
    provider = data.get("provider:openai") or {}
    return provider.get("api_key"), provider.get("base_url")


def cmd_run(args: argparse.Namespace) -> int:
    root = _project_root()
    _load_dotenv(root / ".env")

    db_path = Path(args.db_path) if args.db_path else root / "data" / "enterprise.db"
    if not db_path.exists():
        build_database(db_path=db_path, seed=42)

    semantics_dir = root / "semantics"
    owl_engine = EnterpriseOntologyEngine(
        db_path=db_path,
        ontology_path=semantics_dir / "ontology.ttl",
        shapes_path=semantics_dir / "shapes.ttl",
    )
    # Pre-warm SHACL validation cache on the main thread before spawning workers
    owl_engine._ensure_shacl_validated()
    owl_lock = threading.Lock()

    arms = args.arms if args.arms else list(ARMS)
    for a in arms:
        if a not in ARMS:
            raise ValueError(f"Invalid arm '{a}'. Choose from {ARMS}")

    tasks = get_tasks(tiers=args.tiers, task_ids=args.tasks)
    if not tasks:
        raise ValueError("No tasks matched the provided --tiers / --tasks filters.")

    if args.mock:
        runner: Any = MockAgentRunner(model=args.model or "mock-simulator-v1")
    else:
        secret_key, secret_url = (None, None)
        if args.secrets_file:
            secret_key, secret_url = _load_secrets_file(Path(args.secrets_file))
        api_key = args.api_key or secret_key or os.environ.get("OPENAI_API_KEY")
        base_url = args.base_url or secret_url or os.environ.get("OPENAI_BASE_URL") or None
        model = args.model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        if not api_key:
            print(
                "Error: OPENAI_API_KEY is required when not using --mock. "
                "Set it in .env, environment variables, --secrets-file, or pass --api-key.",
                file=sys.stderr,
            )
            return 2
        runner = OpenAIAgentRunner(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=args.temperature,
            max_turns=args.max_turns,
        )

    out_dir = Path(args.output_dir) if args.output_dir else root / "results" / "latest"
    out_dir.mkdir(parents=True, exist_ok=True)
    traces_path = out_dir / "traces.jsonl"

    existing_ok: dict[tuple[str, str, int], dict[str, Any]] = {}
    if args.resume and traces_path.exists():
        for raw_line in traces_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("error") is None:
                key = (str(rec["arm"]), str(rec["task_id"]), int(rec.get("run_index", 0)))
                existing_ok[key] = rec

    all_jobs: list[tuple[str, Any, int]] = []
    pending_jobs: list[tuple[str, Any, int]] = []
    for run_idx in range(args.runs):
        for arm in arms:
            for task in tasks:
                key = (arm, task.task_id, run_idx)
                all_jobs.append((arm, task, run_idx))
                if key not in existing_ok:
                    pending_jobs.append((arm, task, run_idx))

    if existing_ok:
        print(
            f"Resume enabled: {len(existing_ok)} completed evaluations kept; "
            f"{len(pending_jobs)} remaining out of {len(all_jobs)} total.",
            flush=True,
        )

    # Rewrite checkpoint with preserved valid records before running remaining jobs
    trace_dicts: list[dict[str, Any]] = list(existing_ok.values())
    with traces_path.open("w", encoding="utf-8") as f:
        for d in trace_dicts:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    write_lock = threading.Lock()
    total_target = len(all_jobs)
    done_count = len(trace_dicts)

    def _record_trace(tr: RunTrace) -> None:
        nonlocal done_count
        d = tr.to_dict()
        with write_lock:
            done_count += 1
            trace_dicts.append(d)
            with traces_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
            status = "ERR" if tr.error else ("PASS" if tr.passed else "FAIL")
            if not args.mock or done_count == total_target or done_count % 40 == 0:
                print(
                    f"[{done_count}/{total_target}] run={tr.run_index} arm={tr.arm:12s} "
                    f"task={tr.task_id} -> {status} (turns={tr.turns}, tok={tr.total_tokens}, {tr.latency_ms/1000:.1f}s)",
                    flush=True,
                )

    if pending_jobs:
        if args.concurrency <= 1 or args.mock:
            for arm, task, run_idx in pending_jobs:
                tr = runner.run_task(
                    arm=arm,
                    task=task,
                    run_index=run_idx,
                    db_path=db_path,
                    semantics_dir=semantics_dir,
                    owl_engine=owl_engine,
                    owl_lock=owl_lock,
                )
                _record_trace(tr)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futs = [
                    pool.submit(
                        runner.run_task,
                        arm,
                        task,
                        run_idx,
                        db_path,
                        semantics_dir,
                        owl_engine,
                        owl_lock,
                    )
                    for arm, task, run_idx in pending_jobs
                ]
                for fut in concurrent.futures.as_completed(futs):
                    _record_trace(fut.result())

    # Sort deterministically for clean trace files
    arm_order = {a: i for i, a in enumerate(ARMS)}
    trace_dicts.sort(key=lambda t: (int(t.get("run_index", 0)), arm_order.get(t["arm"], 99), t["task_id"]))
    with traces_path.open("w", encoding="utf-8") as f:
        for d in trace_dicts:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    in_price = float(
        args.input_price
        if args.input_price is not None
        else os.environ.get("INPUT_PRICE_PER_1M", "0.15")
    )
    out_price = float(
        args.output_price
        if args.output_price is not None
        else os.environ.get("OUTPUT_PRICE_PER_1M", "0.60")
    )

    summary_path, report_path, summary = write_reports(
        trace_paths=[traces_path],
        output_dir=out_dir,
        project_root=root,
        input_price_per_1m=in_price,
        output_price_per_1m=out_price,
        owl_stats=owl_engine.stats(),
    )

    print(f"Completed {len(trace_dicts)} evaluations across {len(arms)} arms x {len(tasks)} tasks x {args.runs} run(s).")
    print(f"Traces : {traces_path}")
    print(f"Summary: {summary_path}")
    print(f"Report : {report_path}")
    for mname, mdata in summary["models"].items():
        print(f"\nModel: {mname}")
        for arm in mdata["arms"]:
            o = mdata["overall_by_arm"][arm]
            print(f"  - {arm:14s}: {o['passed']}/{o['n']} ({o['accuracy']*100:.1f}%) | avg_tok={o['avg_total_tokens']:.0f}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = _project_root()
    _load_dotenv(root / ".env")
    trace_paths = [Path(p) for p in args.traces]
    out_dir = Path(args.output_dir) if args.output_dir else trace_paths[0].parent

    db_path = root / "data" / "enterprise.db"
    owl_stats = None
    if db_path.exists():
        engine = EnterpriseOntologyEngine(
            db_path=db_path,
            ontology_path=root / "semantics" / "ontology.ttl",
            shapes_path=root / "semantics" / "shapes.ttl",
        )
        owl_stats = engine.stats()

    in_price = float(
        args.input_price
        if args.input_price is not None
        else os.environ.get("INPUT_PRICE_PER_1M", "0.15")
    )
    out_price = float(
        args.output_price
        if args.output_price is not None
        else os.environ.get("OUTPUT_PRICE_PER_1M", "0.60")
    )

    summary_path, report_path, _ = write_reports(
        trace_paths=trace_paths,
        output_dir=out_dir,
        project_root=root,
        input_price_per_1m=in_price,
        output_price_per_1m=out_price,
        owl_stats=owl_stats,
    )
    print(f"Generated summary: {summary_path}")
    print(f"Generated report : {report_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ovr-bench",
        description="Ontology (OWL 2 RL + SHACL) vs README.md vs Bare RDS benchmark runner.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_db = sub.add_parser("build-db", help="Generate deterministic enterprise.db SQLite database.")
    p_db.add_argument("--db-path", default=None)
    p_db.add_argument("--seed", type=int, default=42)
    p_db.set_defaults(func=cmd_build_db)

    p_ver = sub.add_parser("verify", help="Verify 40 tasks, OWL 2 RL closure, SHACL parity & R01-R28 equivalence.")
    p_ver.add_argument("--db-path", default=None)
    p_ver.set_defaults(func=cmd_verify)

    p_run = sub.add_parser("run", help="Run multi-arm benchmark evaluations.")
    p_run.add_argument("--arms", nargs="+", choices=list(ARMS), default=None)
    p_run.add_argument("--tiers", nargs="+", type=int, choices=[1, 2, 3, 4], default=None)
    p_run.add_argument("--tasks", nargs="+", default=None, help="Specific task IDs, e.g. T2_01 T4_08")
    p_run.add_argument("--runs", type=int, default=1, help="Number of repeated runs per task-arm pair.")
    p_run.add_argument("--model", default=None)
    p_run.add_argument("--base-url", default=None)
    p_run.add_argument("--api-key", default=None)
    p_run.add_argument("--secrets-file", default=None, help="JSON file with provider:openai credentials.")
    p_run.add_argument("--resume", action="store_true", help="Resume from existing traces.jsonl in output-dir.")
    p_run.add_argument("--temperature", type=float, default=0.0)
    p_run.add_argument("--max-turns", type=int, default=12)
    p_run.add_argument("--concurrency", type=int, default=4)
    p_run.add_argument("--mock", action="store_true", help="Run offline deterministic pipeline simulation.")
    p_run.add_argument("--db-path", default=None)
    p_run.add_argument("--output-dir", default=None)
    p_run.add_argument("--input-price", type=float, default=None)
    p_run.add_argument("--output-price", type=float, default=None)
    p_run.set_defaults(func=cmd_run)

    p_rep = sub.add_parser("report", help="Generate report.md and summary.json from one or more traces.jsonl files.")
    p_rep.add_argument("traces", nargs="+", help="Path(s) to traces.jsonl")
    p_rep.add_argument("--output-dir", default=None)
    p_rep.add_argument("--input-price", type=float, default=None)
    p_rep.add_argument("--output-price", type=float, default=None)
    p_rep.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
