"""Real OWL 2 RL + SHACL reasoning engine over the Meridian enterprise database.

Loads `semantics/ontology.ttl` and `semantics/shapes.ttl`, materializes the ABox
from SQLite (`enterprise.db`), runs `owlrl.DeductiveClosure(owlrl.OWLRL_Semantics)`,
and exposes deterministic query & SHACL validation methods for Arm C2 (`owl_reasoner`).
"""

from __future__ import annotations

import re
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import owlrl
import pyshacl
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

ONT_NS = "http://meridian.example/ont#"
ONT = Namespace(ONT_NS)
SH = Namespace("http://www.w3.org/ns/shacl#")

DEFAULT_SPARQL_PREFIXES = """PREFIX : <http://meridian.example/ont#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""


def _local_name(node: Any) -> str:
    s = str(node)
    if s.startswith(ONT_NS):
        return s[len(ONT_NS):]
    if "#" in s:
        return s.rsplit("#", 1)[-1]
    if "/" in s:
        return s.rsplit("/", 1)[-1]
    return s


def _to_json_value(val: Any) -> Any:
    if isinstance(val, URIRef):
        return _local_name(val)
    if isinstance(val, Literal):
        py_val = val.toPython()
        if isinstance(py_val, Decimal):
            return float(py_val)
        if isinstance(py_val, (int, float, bool)):
            return py_val
        return str(py_val)
    return str(val)


def _normalize_term(name: str) -> URIRef:
    clean = name.strip()
    if clean.startswith("<") and clean.endswith(">"):
        clean = clean[1:-1]
    if clean.startswith("http://") or clean.startswith("https://"):
        return URIRef(clean)
    if clean.startswith(":"):
        clean = clean[1:]
    return ONT[clean]


class EnterpriseOntologyEngine:
    """In-memory OWL 2 RL materialized knowledge graph + SHACL validator."""

    def __init__(
        self,
        db_path: Path,
        ontology_path: Path,
        shapes_path: Path,
    ) -> None:
        self.db_path = Path(db_path)
        self.ontology_path = Path(ontology_path)
        self.shapes_path = Path(shapes_path)

        self.graph = Graph()
        self.graph.bind("", ONT)
        self.graph.parse(str(self.ontology_path), format="turtle")

        self.shapes_graph = Graph()
        self.shapes_graph.parse(str(self.shapes_path), format="turtle")

        self._materialize_abox_from_sqlite()
        self.raw_triple_count = len(self.graph)

        t0 = time.perf_counter()
        owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(self.graph)
        self.reasoning_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        self.inferred_triple_count = len(self.graph)

        self._shacl_results_graph: Graph | None = None
        self._shacl_ms: float | None = None

    def _materialize_abox_from_sqlite(self) -> None:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()

            cat_map: dict[str, str] = {}
            for row in cur.execute("SELECT cat_id, cat_cd FROM prod_cat_tree"):
                cat_map[row["cat_id"]] = row["cat_cd"]

            for row in cur.execute(
                "SELECT sku_id, sku_cd, sku_nm, cat_id, dual_use_flg, actv_flg FROM t_sku_mst"
            ):
                uri = ONT[row["sku_id"]]
                cat_cls = ONT[cat_map[row["cat_id"]]]
                self.graph.add((uri, RDF.type, cat_cls))
                self.graph.add((uri, ONT.entityId, Literal(row["sku_id"])))
                self.graph.add((uri, ONT.skuCode, Literal(row["sku_cd"])))
                self.graph.add((uri, ONT.entityName, Literal(row["sku_nm"])))
                self.graph.add(
                    (uri, ONT.hasDualUseFlag, Literal(bool(row["dual_use_flg"]), datatype=XSD.boolean))
                )

            for row in cur.execute(
                "SELECT acct_id, acct_nm, prnt_acct_id, seg_cd, sts_cd, sanc_flg FROM t_acct_mst"
            ):
                uri = ONT[row["acct_id"]]
                self.graph.add((uri, RDF.type, ONT.Account))
                self.graph.add((uri, ONT.entityId, Literal(row["acct_id"])))
                self.graph.add((uri, ONT.entityName, Literal(row["acct_nm"])))
                self.graph.add((uri, ONT.accountSegment, Literal(row["seg_cd"])))
                self.graph.add(
                    (uri, ONT.accountStatusCode, Literal(int(row["sts_cd"]), datatype=XSD.integer))
                )
                self.graph.add(
                    (
                        uri,
                        ONT.hasDirectSanctionFlag,
                        Literal(bool(row["sanc_flg"]), datatype=XSD.boolean),
                    )
                )
                if row["prnt_acct_id"]:
                    self.graph.add((uri, ONT.directParentAccount, ONT[row["prnt_acct_id"]]))

            for row in cur.execute(
                "SELECT vndr_id, vndr_nm, prnt_vndr_id, sts_cd FROM t_vndr_mst"
            ):
                uri = ONT[row["vndr_id"]]
                self.graph.add((uri, RDF.type, ONT.Vendor))
                self.graph.add((uri, ONT.entityId, Literal(row["vndr_id"])))
                self.graph.add((uri, ONT.entityName, Literal(row["vndr_nm"])))
                self.graph.add((uri, ONT.hasVendorStatusCode, Literal(row["sts_cd"])))
                if row["prnt_vndr_id"]:
                    self.graph.add((uri, ONT.directParentVendor, ONT[row["prnt_vndr_id"]]))

            for row in cur.execute(
                "SELECT cert_id, vndr_id, cert_type, valid_from, valid_to, revoked_flg FROM vendor_certs"
            ):
                c_uri = ONT[row["cert_id"]]
                v_uri = ONT[row["vndr_id"]]
                self.graph.add((c_uri, RDF.type, ONT.VendorCertificate))
                self.graph.add((v_uri, ONT.hasCertificate, c_uri))
                self.graph.add((c_uri, ONT.entityId, Literal(row["cert_id"])))
                self.graph.add((c_uri, ONT.certType, Literal(row["cert_type"])))
                self.graph.add((c_uri, ONT.validFrom, Literal(row["valid_from"])))
                self.graph.add((c_uri, ONT.validTo, Literal(row["valid_to"])))
                self.graph.add(
                    (c_uri, ONT.isRevoked, Literal(bool(row["revoked_flg"]), datatype=XSD.boolean))
                )

            for row in cur.execute(
                """
                SELECT po_req_id, vndr_id, sku_id, qty, unit_prc_usd, req_dt,
                       ship_mode, is_expedited, approver_lvl, req_sts, override_note
                FROM t_po_req
                """
            ):
                po_uri = ONT[row["po_req_id"]]
                self.graph.add((po_uri, RDF.type, ONT.PurchaseOrderRequest))
                self.graph.add((po_uri, ONT.entityId, Literal(row["po_req_id"])))
                self.graph.add((po_uri, ONT.hasVendor, ONT[row["vndr_id"]]))
                self.graph.add((po_uri, ONT.hasSKU, ONT[row["sku_id"]]))
                self.graph.add((po_uri, ONT.poQty, Literal(int(row["qty"]), datatype=XSD.integer)))
                self.graph.add(
                    (
                        po_uri,
                        ONT.poUnitPriceUsd,
                        Literal(Decimal(str(row["unit_prc_usd"])), datatype=XSD.decimal),
                    )
                )
                self.graph.add((po_uri, ONT.reqDate, Literal(row["req_dt"])))
                self.graph.add((po_uri, ONT.shipMode, Literal(row["ship_mode"])))
                self.graph.add(
                    (po_uri, ONT.isExpedited, Literal(bool(row["is_expedited"]), datatype=XSD.boolean))
                )
                self.graph.add(
                    (po_uri, ONT.approverLevel, Literal(row["approver_lvl"]))
                )
                self.graph.add((po_uri, ONT.reqStatus, Literal(row["req_sts"])))
                if row["override_note"]:
                    self.graph.add(
                        (po_uri, ONT.overrideNote, Literal(row["override_note"]))
                    )
        finally:
            conn.close()

    def stats(self) -> dict[str, Any]:
        return {
            "raw_triples": self.raw_triple_count,
            "inferred_triples": self.inferred_triple_count,
            "new_inferred_triples": self.inferred_triple_count - self.raw_triple_count,
            "reasoning_ms": self.reasoning_ms,
            "shacl_ms": self._shacl_ms,
        }

    def list_classes_and_members(self, class_name: str) -> dict[str, Any]:
        cls_uri = _normalize_term(class_name)
        if not (cls_uri, RDF.type, OWL.Class) in self.graph and not (cls_uri, RDF.type, RDFS.Class) in self.graph:
            # Check if it appears as a class in subClassOf or rdf:type
            if not any(self.graph.triples((None, RDF.type, cls_uri))) and not any(
                self.graph.triples((cls_uri, RDFS.subClassOf, None))
            ):
                return {"error": f"Unknown ontology class: {class_name}"}

        subclasses: set[str] = set()
        for sub in self.graph.subjects(RDFS.subClassOf, cls_uri):
            if isinstance(sub, URIRef) and sub != cls_uri and sub != OWL.Nothing and str(sub).startswith(ONT_NS):
                subclasses.add(_local_name(sub))

        superclasses: set[str] = set()
        for sup in self.graph.objects(cls_uri, RDFS.subClassOf):
            if isinstance(sup, URIRef) and sup != cls_uri and sup != OWL.Thing and str(sup).startswith(ONT_NS):
                superclasses.add(_local_name(sup))

        disjoint: set[str] = set()
        for d in self.graph.objects(cls_uri, OWL.disjointWith):
            if isinstance(d, URIRef) and str(d).startswith(ONT_NS):
                disjoint.add(_local_name(d))
        for d in self.graph.subjects(OWL.disjointWith, cls_uri):
            if isinstance(d, URIRef) and str(d).startswith(ONT_NS):
                disjoint.add(_local_name(d))

        members: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for inst in self.graph.subjects(RDF.type, cls_uri):
            if isinstance(inst, BNode) or not str(inst).startswith(ONT_NS):
                continue
            eid = _local_name(inst)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            name_lit = next(self.graph.objects(inst, ONT.entityName), None)
            sku_cd = next(self.graph.objects(inst, ONT.skuCode), None)
            entry: dict[str, Any] = {"id": eid}
            if name_lit is not None:
                entry["name"] = str(name_lit)
            if sku_cd is not None:
                entry["sku_cd"] = str(sku_cd)
            members.append(entry)

        members.sort(key=lambda x: x["id"])
        return {
            "class_name": _local_name(cls_uri),
            "subclasses": sorted(subclasses),
            "superclasses": sorted(superclasses),
            "disjoint_classes": sorted(disjoint),
            "member_count": len(members),
            "members": members,
        }

    def get_entity_inferences(self, entity_id: str) -> dict[str, Any]:
        ent_uri = _normalize_term(entity_id)
        if not any(self.graph.triples((ent_uri, None, None))):
            return {"error": f"Entity not found in ontology graph: {entity_id}"}

        inferred_types: set[str] = set()
        for t in self.graph.objects(ent_uri, RDF.type):
            if isinstance(t, URIRef) and str(t).startswith(ONT_NS):
                inferred_types.add(_local_name(t))

        ancestors: set[str] = set()
        for pred in (ONT.hasAncestorAccount, ONT.hasAncestorVendor):
            for anc in self.graph.objects(ent_uri, pred):
                if isinstance(anc, URIRef) and anc != ent_uri and str(anc).startswith(ONT_NS):
                    ancestors.add(_local_name(anc))

        direct_parent: str | None = None
        for pred in (ONT.directParentAccount, ONT.directParentVendor):
            p = next(self.graph.objects(ent_uri, pred), None)
            if isinstance(p, URIRef):
                direct_parent = _local_name(p)

        props: dict[str, Any] = {}
        for p, o in self.graph.predicate_objects(ent_uri):
            if p in (RDF.type, ONT.hasAncestorAccount, ONT.hasAncestorVendor, ONT.hasCertificate):
                continue
            if isinstance(p, URIRef) and str(p).startswith(ONT_NS):
                key = _local_name(p)
                val = _to_json_value(o)
                if key not in props:
                    props[key] = val

        certs: list[dict[str, Any]] = []
        for c_uri in self.graph.objects(ent_uri, ONT.hasCertificate):
            certs.append(
                {
                    "cert_id": _local_name(c_uri),
                    "cert_type": str(next(self.graph.objects(c_uri, ONT.certType), "")),
                    "valid_from": str(next(self.graph.objects(c_uri, ONT.validFrom), "")),
                    "valid_to": str(next(self.graph.objects(c_uri, ONT.validTo), "")),
                    "is_revoked": bool(next(self.graph.objects(c_uri, ONT.isRevoked), Literal(False)).toPython()),
                }
            )
        certs.sort(key=lambda c: c["cert_id"])

        out: dict[str, Any] = {
            "entity_id": _local_name(ent_uri),
            "inferred_classes": sorted(inferred_types),
            "direct_parent": direct_parent,
            "transitive_ancestors": sorted(ancestors),
            "properties": props,
        }
        if certs:
            out["certificates"] = certs
        return out

    def run_sparql(self, query: str) -> dict[str, Any]:
        stripped = query.strip()
        if re.search(r"\b(INSERT|DELETE|LOAD|CLEAR|CREATE|DROP|MOVE|COPY|ADD)\b", stripped, re.IGNORECASE):
            return {"error": "Read-only SPARQL SELECT queries only."}
        if not re.search(r"\bPREFIX\s*:", stripped, re.IGNORECASE):
            full_query = f"{DEFAULT_SPARQL_PREFIXES}\n{stripped}"
        else:
            full_query = stripped

        try:
            res = self.graph.query(full_query)
            vars_list = [str(v) for v in (res.vars or [])]
            rows: list[dict[str, Any]] = []
            truncated = False
            for idx, r in enumerate(res):
                if idx >= 200:
                    truncated = True
                    break
                row_dict = {}
                for v_name, cell in zip(vars_list, r):
                    row_dict[v_name] = None if cell is None else _to_json_value(cell)
                rows.append(row_dict)
            return {
                "columns": vars_list,
                "row_count": len(rows),
                "truncated": truncated,
                "rows": rows,
            }
        except Exception as exc:
            return {"error": f"SPARQL execution error: {exc}"}

    def _ensure_shacl_validated(self, force: bool = False) -> Graph:
        if self._shacl_results_graph is None or force:
            t0 = time.perf_counter()
            _, results_graph, _ = pyshacl.validate(
                self.graph,
                shacl_graph=self.shapes_graph,
                inference=None,
                advanced=True,
                abort_on_first=False,
                meta_shacl=False,
                debug=False,
            )
            self._shacl_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            self._shacl_results_graph = results_graph
        return self._shacl_results_graph

    def validate_po_shacl(self, po_req_id: str, force_revalidate: bool = False) -> dict[str, Any]:
        po_uri = _normalize_term(po_req_id)
        if not (po_uri, RDF.type, ONT.PurchaseOrderRequest) in self.graph:
            return {"error": f"PurchaseOrderRequest not found: {po_req_id}"}

        results_graph = self._ensure_shacl_validated(force=force_revalidate)
        hard_violations: list[dict[str, str]] = []
        escalations: list[dict[str, str]] = []
        violated_rules: set[str] = set()

        for result in results_graph.subjects(RDF.type, SH.ValidationResult):
            focus = next(results_graph.objects(result, SH.focusNode), None)
            if focus != po_uri:
                continue
            severity = next(results_graph.objects(result, SH.resultSeverity), SH.Violation)
            source_shape = next(results_graph.objects(result, SH.sourceShape), None)
            msg = str(next(results_graph.objects(result, SH.resultMessage), ""))
            rule_id = ""
            if source_shape is not None:
                r_lit = next(self.shapes_graph.objects(source_shape, ONT.ruleId), None)
                if r_lit is not None:
                    rule_id = str(r_lit)
            if not rule_id and ":" in msg:
                rule_id = msg.split(":", 1)[0].strip()

            item = {"rule_id": rule_id, "message": msg}
            if rule_id:
                violated_rules.add(rule_id)

            if severity == SH.Warning:
                escalations.append(item)
            else:
                hard_violations.append(item)

        hard_violations.sort(key=lambda x: x["rule_id"])
        escalations.sort(key=lambda x: x["rule_id"])

        if hard_violations:
            decision = "REJECT"
        elif escalations:
            decision = "ESCALATE"
        else:
            decision = "APPROVE"

        return {
            "po_req_id": _local_name(po_uri),
            "conforms_hard_rules": len(hard_violations) == 0,
            "decision": decision,
            "violated_rules": sorted(violated_rules),
            "hard_violations": hard_violations,
            "escalations": escalations,
            "shacl_validation_ms": self._shacl_ms,
        }
