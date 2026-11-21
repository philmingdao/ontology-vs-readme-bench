"""Shared read-only SQLite tools, Arm-specific OWL/SHACL tools, and prompt builders."""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from bench.owl_engine import EnterpriseOntologyEngine

ARMS = ("bare_rds", "readme_md", "raw_owl", "owl_reasoner")


def build_rds_context(db_path: Path, sample_rows: int = 3) -> str:
    """Extract SQLite DDL and N sample rows per table for the shared RDS baseline."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        tables = [
            row[0]
            for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        sections: list[str] = ["## Database Schema (SQLite DDL & Sample Rows)\n"]
        for tbl in tables:
            ddl_row = cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
            ddl_sql = ddl_row[0] if ddl_row else ""
            rows = cur.execute(f"SELECT * FROM {tbl} LIMIT ?", (sample_rows,)).fetchall()
            col_names = [desc[0] for desc in cur.description] if cur.description else []
            sections.append(f"### Table `{tbl}`\n```sql\n{ddl_sql};\n```")
            if rows:
                header = "| " + " | ".join(col_names) + " |"
                sep = "| " + " | ".join("---" for _ in col_names) + " |"
                body = "\n".join(
                    "| " + " | ".join("NULL" if c is None else str(c) for c in r) + " |"
                    for r in rows
                )
                sections.append(f"Sample rows (`LIMIT {sample_rows}`):\n{header}\n{sep}\n{body}\n")
        return "\n".join(sections)
    finally:
        conn.close()


def build_system_prompt(arm: str, db_path: Path, semantics_dir: Path) -> str:
    if arm not in ARMS:
        raise ValueError(f"Unknown arm: {arm}. Expected one of {ARMS}")

    rds_block = build_rds_context(db_path)

    base_instructions = """You are an enterprise data analyst and compliance agent working with Meridian Industrial & MedTech Corp's SQLite database.

### Tool & Answer Submission Rules
1. Explore and verify facts using your tools before submitting an answer. Never guess numbers, entity IDs, or compliance outcomes.
2. When ready, you MUST call `submit_final_answer` with the exact payload shape requested:
   - Numeric scalar questions: `{"value": <number>}`
   - String scalar questions: `{"value": "<string>"}`
   - Table questions: `{"columns": ["col1", ...], "rows": [[val1, ...], ...]}` matching the requested columns and sort order.
   - Purchase Order compliance evaluation questions: `{"decision": "APPROVE" | "ESCALATE" | "REJECT", "violated_rules": ["R20", ...]}` (use `[]` if approved; list ALL violated rule codes if rejected or escalated).
3. In SQLite, use `WITH RECURSIVE` if you need to traverse parent-child hierarchies via SQL.
"""

    if arm == "bare_rds":
        return f"{base_instructions}\n{rds_block}"

    if arm == "readme_md":
        readme_text = (semantics_dir / "enterprise_readme.md").read_text(encoding="utf-8")
        return (
            f"{base_instructions}\n"
            f"## Enterprise Semantic Guide (`enterprise_readme.md`)\n\n{readme_text}\n\n"
            f"{rds_block}"
        )

    owl_text = (semantics_dir / "ontology.ttl").read_text(encoding="utf-8")
    if arm == "raw_owl":
        return (
            f"{base_instructions}\n"
            f"## Enterprise OWL 2 Ontology (`ontology.ttl`)\n\n```turtle\n{owl_text}\n```\n\n"
            f"{rds_block}"
        )

    # arm == "owl_reasoner"
    reasoner_note = """### OWL 2 RL + SHACL Reasoning Tools Available
In addition to SQLite tools, you have access to an in-memory **OWL 2 RL materialized knowledge graph** (`owlrl.OWLRL_Semantics`) and **SHACL compliance validator** (`pyshacl` using `shapes.ttl`) built from `enterprise.db` and `ontology.ttl`:
- `owl_list_classes_and_members(class_name)`: inspect inferred subclasses, superclasses, disjoint classes, and all inferred members (e.g., `SanctionedAccount`, `SanctionedVendor`, `MedicalDevice`, `ExportRegulatedItem`, `RegulatedProduct`).
- `owl_get_entity_inferences(entity_id)`: inspect all inferred OWL classes, transitive ancestors, and certificates for an entity (account, vendor, SKU, or PO).
- `owl_run_sparql(query)`: execute read-only SPARQL `SELECT` queries against the materialized OWL 2 RL graph.
- `owl_validate_po_shacl(po_req_id)`: run deterministic SHACL policy validation (`R20`–`R27`) on a PO request over the OWL 2 RL inferred graph.
Use SQL for quantitative aggregations (bookings, revenue, cash, counts) and combine with OWL/SHACL tools whenever hierarchy inference, dual-use classification, or PO policy validation is involved.
"""
    return (
        f"{base_instructions}\n{reasoner_note}\n"
        f"## Enterprise OWL 2 Ontology (`ontology.ttl`)\n\n```turtle\n{owl_text}\n```\n\n"
        f"{rds_block}"
    )


def get_tool_schemas(arm: str) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "list_tables",
                "description": "List all table names in the enterprise SQLite database.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_table_schema",
                "description": "Get DDL and up to 5 sample rows for a specific SQLite table.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "Exact SQLite table name"}
                    },
                    "required": ["table_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_sql",
                "description": "Execute a read-only SQLite SELECT / WITH query and return columns and up to 200 rows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Read-only SQL SELECT or WITH query"}
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_final_answer",
                "description": "Submit the final structured answer to finish the task.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {
                            "description": "Structured answer payload: {value: ...}, {columns: [...], rows: [...]}, or {decision: '...', violated_rules: [...]}."
                        }
                    },
                    "required": ["answer"],
                },
            },
        },
    ]

    if arm == "owl_reasoner":
        tools.extend(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "owl_list_classes_and_members",
                        "description": "Query the OWL 2 RL inferred graph for a class's subclasses, superclasses, disjoint classes, and all inferred members.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "class_name": {
                                    "type": "string",
                                    "description": "OWL class local name, e.g. MedicalDevice, ExportRegulatedItem, RegulatedProduct, SanctionedVendor, SanctionedAccount",
                                }
                            },
                            "required": ["class_name"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "owl_get_entity_inferences",
                        "description": "Get all OWL 2 RL inferred classes, transitive ancestors, properties, and certificates for an entity ID (e.g. V_BLK_L3, A_SANC_L4, SKU_HW_DUAL, PO_002).",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "entity_id": {"type": "string", "description": "Entity ID"}
                            },
                            "required": ["entity_id"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "owl_run_sparql",
                        "description": "Execute a read-only SPARQL SELECT query against the OWL 2 RL materialized graph (default PREFIX : <http://meridian.example/ont#> is auto-injected if omitted).",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "SPARQL SELECT query"}
                            },
                            "required": ["query"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "owl_validate_po_shacl",
                        "description": "Run deterministic SHACL compliance validation (rules R20-R27) on a PurchaseOrderRequest using the OWL 2 RL inferred graph.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "po_req_id": {
                                    "type": "string",
                                    "description": "Purchase Order request ID, e.g. PO_001",
                                }
                            },
                            "required": ["po_req_id"],
                        },
                    },
                },
            ]
        )

    return tools


class ToolExecutor:
    """Executes SQLite and optional OWL/SHACL tools for a single agent run."""

    def __init__(
        self,
        db_path: Path,
        arm: str,
        owl_engine: EnterpriseOntologyEngine | None = None,
        owl_lock: threading.Lock | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.arm = arm
        self.owl_engine = owl_engine
        self.owl_lock = owl_lock

    def _connect_ro(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def execute(self, name: str, arguments: dict[str, Any]) -> tuple[str, Any | None]:
        if name == "submit_final_answer":
            payload = arguments.get("answer", arguments)
            return json.dumps({"status": "submitted", "answer": payload}), payload

        if name == "list_tables":
            conn = self._connect_ro()
            try:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                return json.dumps({"tables": [r[0] for r in rows]}), None
            finally:
                conn.close()

        if name == "get_table_schema":
            tbl = str(arguments.get("table_name", "")).strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tbl):
                return json.dumps({"error": f"Invalid table name: {tbl}"}), None
            conn = self._connect_ro()
            try:
                ddl = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
                ).fetchone()
                if not ddl:
                    return json.dumps({"error": f"Table not found: {tbl}"}), None
                cur = conn.execute(f"SELECT * FROM {tbl} LIMIT 5")
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = [list(r) for r in cur.fetchall()]
                return json.dumps({"table": tbl, "ddl": ddl[0], "columns": cols, "sample_rows": rows}), None
            finally:
                conn.close()

        if name == "run_sql":
            query = str(arguments.get("query", "")).strip()
            if not re.match(r"^(SELECT|WITH|EXPLAIN)\b", query, re.IGNORECASE):
                return json.dumps({"error": "Only read-only SELECT or WITH queries are permitted."}), None
            if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REPLACE)\b", query, re.IGNORECASE):
                return json.dumps({"error": "Write/DDL keywords are not permitted in run_sql."}), None
            conn = self._connect_ro()
            try:
                cur = conn.execute(query)
                cols = [d[0] for d in cur.description] if cur.description else []
                fetched = cur.fetchmany(201)
                truncated = len(fetched) > 200
                rows = [list(r) for r in fetched[:200]]
                return (
                    json.dumps(
                        {
                            "columns": cols,
                            "row_count": len(rows),
                            "truncated": truncated,
                            "rows": rows,
                        }
                    ),
                    None,
                )
            except Exception as exc:
                return json.dumps({"error": f"SQL error: {exc}"}), None
            finally:
                conn.close()

        if self.arm == "owl_reasoner" and self.owl_engine is not None:
            lock_ctx = self.owl_lock if self.owl_lock is not None else contextlib.nullcontext()
            with lock_ctx:
                if name == "owl_list_classes_and_members":
                    res = self.owl_engine.list_classes_and_members(str(arguments.get("class_name", "")))
                    return json.dumps(res), None
                if name == "owl_get_entity_inferences":
                    res = self.owl_engine.get_entity_inferences(str(arguments.get("entity_id", "")))
                    return json.dumps(res), None
                if name == "owl_run_sparql":
                    res = self.owl_engine.run_sparql(str(arguments.get("query", "")))
                    return json.dumps(res), None
                if name == "owl_validate_po_shacl":
                    res = self.owl_engine.validate_po_shacl(str(arguments.get("po_req_id", "")))
                    return json.dumps(res), None

        return json.dumps({"error": f"Unknown or disallowed tool for arm {self.arm}: {name}"}), None
