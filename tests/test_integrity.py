"""Integrity, OWL 2 RL reasoning, SHACL parity, and CLI smoke tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bench.cli import verify_all
from bench.db_builder import build_database
from bench.evaluator import evaluate_submission
from bench.owl_engine import EnterpriseOntologyEngine
from bench.tasks import TASKS, compute_ground_truth

ROOT = Path(__file__).resolve().parents[1]


def test_db_build_and_hierarchies(tmp_path: Path) -> None:
    db_path = tmp_path / "enterprise.db"
    counts = build_database(db_path=db_path, seed=42)
    assert counts["t_acct_mst"] >= 20
    assert counts["t_vndr_mst"] >= 15
    assert counts["t_sku_mst"] == 32
    assert counts["t_ord_hdr"] >= 230
    assert counts["t_ord_ln"] >= 450
    assert counts["t_po_req"] == 15

    conn = sqlite3.connect(str(db_path))
    try:
        # Check 4-level product category depth (CAT_ROOT -> CAT_REG -> CAT_MED -> CAT_MED_ACT -> CAT_MED_IMP)
        depth = conn.execute(
            """
            WITH RECURSIVE tree(cat_id, lvl) AS (
                SELECT cat_id, 1 FROM prod_cat_tree WHERE prnt_cat_id IS NULL
                UNION ALL
                SELECT c.cat_id, t.lvl + 1 FROM prod_cat_tree c JOIN tree t ON c.prnt_cat_id = t.cat_id
            )
            SELECT MAX(lvl) FROM tree
            """
        ).fetchone()[0]
        assert depth == 5  # 4 edges below root
    finally:
        conn.close()


def test_owl_rl_inferences_and_shacl_parity(tmp_path: Path) -> None:
    db_path = tmp_path / "enterprise.db"
    build_database(db_path=db_path, seed=42)

    engine = EnterpriseOntologyEngine(
        db_path=db_path,
        ontology_path=ROOT / "semantics" / "ontology.ttl",
        shapes_path=ROOT / "semantics" / "shapes.ttl",
    )

    # 1. 4-level category subsumption for ImplantableDevice
    imp_inf = engine.get_entity_inferences("SKU_IMP_01")
    for cls_name in ("ImplantableDevice", "ActiveMedicalDevice", "MedicalDevice", "RegulatedProduct", "Product"):
        assert cls_name in imp_inf["inferred_classes"]

    # 2. Dual-use IndustrialHardware -> ExportRegulatedItem -> RegulatedProduct
    dual_inf = engine.get_entity_inferences("SKU_HW_DUAL")
    assert "IndustrialHardware" in dual_inf["inferred_classes"]
    assert "ExportRegulatedItem" in dual_inf["inferred_classes"]
    assert "RegulatedProduct" in dual_inf["inferred_classes"]

    # 3. 4th-level descendant of sanctioned account inherits SanctionedAccount
    sanc_acct_l4 = engine.get_entity_inferences("A_SANC_L4")
    assert "SanctionedAccount" in sanc_acct_l4["inferred_classes"]
    assert "A_SANC_ROOT" in sanc_acct_l4["transitive_ancestors"]

    # 4. 4th-level descendant of blacklisted vendor inherits SanctionedVendor
    sanc_vndr_l4 = engine.get_entity_inferences("V_BLK_L4")
    assert "SanctionedVendor" in sanc_vndr_l4["inferred_classes"]
    assert "V_BLK_ROOT" in sanc_vndr_l4["transitive_ancestors"]

    # 5. Run full verify_all cross-checks (all 40 tasks, OWL vs SQL, SHACL vs SQL, R01-R28 audit)
    res = verify_all(root=ROOT, db_path=db_path)
    assert res["tasks_verified"] == 40
    assert res["rules_audited"] == 29  # R01..R22, R23a, R23b, R24..R28


def test_evaluator_handles_exact_and_tolerance_cases(tmp_path: Path) -> None:
    db_path = tmp_path / "enterprise.db"
    build_database(db_path=db_path, seed=42)

    t2_01 = next(t for t in TASKS if t.task_id == "T2_01")
    gt_num = compute_ground_truth(db_path, t2_01)
    assert evaluate_submission(t2_01, {"value": gt_num + 0.01}, gt_num).passed
    assert not evaluate_submission(t2_01, {"value": gt_num * 1.15}, gt_num).passed

    t4_08 = next(t for t in TASKS if t.task_id == "T4_08")
    gt_dec = compute_ground_truth(db_path, t4_08)
    assert gt_dec == {"decision": "REJECT", "violated_rules": ["R23b", "R25", "R27"]}
    assert evaluate_submission(
        t4_08,
        {"decision": "reject", "violated_rules": ["R27", "R23B", "R25"]},
        gt_dec,
    ).passed
    partial = evaluate_submission(
        t4_08,
        {"decision": "REJECT", "violated_rules": ["R23b", "R25"]},
        gt_dec,
    )
    assert not partial.passed
    assert partial.decision_exact is True
    assert partial.rules_exact is False
    assert partial.rules_f1 is not None and 0.7 < partial.rules_f1 < 0.9


def test_mock_cli_smoke(tmp_path: Path) -> None:
    from bench.cli import main

    db_path = tmp_path / "enterprise.db"
    out_dir = tmp_path / "mock_out"
    assert main(["build-db", "--db-path", str(db_path)]) == 0
    assert main(["verify", "--db-path", str(db_path)]) == 0
    assert main(["run", "--mock", "--db-path", str(db_path), "--output-dir", str(out_dir)]) == 0

    traces_file = out_dir / "traces.jsonl"
    summary_file = out_dir / "summary.json"
    report_file = out_dir / "report.md"
    assert traces_file.exists()
    assert summary_file.exists()
    assert report_file.exists()

    lines = traces_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 160  # 4 arms * 40 tasks

    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    model_data = next(iter(summary["models"].values()))
    assert set(model_data["overall_by_arm"].keys()) == {"bare_rds", "readme_md", "raw_owl", "owl_reasoner"}
    assert "readme_md_vs_owl_reasoner" in model_data["mcnemar"]
    assert "readme_md_vs_owl_reasoner" in model_data["tost"]
