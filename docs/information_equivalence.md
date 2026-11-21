# Information Equivalence Audit (`R01`–`R28`)

To eliminate confounding information bias between **Arm B (`readme_md`)**, **Arm C1 (`raw_owl`)**, and **Arm C2 (`owl_reasoner`)**, every canonical business rule `R01`–`R28` is represented with **1-to-1 informational equivalence**:

- **No unfair SQL shortcuts in `enterprise_readme.md`**: The Markdown guide deliberately omits copy-paste SQL queries or recursive CTE templates. It communicates definitions, mappings, and rules solely in structured natural language.
- **No missing metric/enum semantics in `ontology.ttl`**: Every table/column glossary and metric definition (`R01`–`R13`) is explicitly encoded on OWL classes via structured annotation properties (`:ruleId`, `:dbTable`, `:dbColumn`, `:enumMapping`, `:formula`, `:hygieneRule`, `:policyRule`) so `raw_owl` carries the exact same facts as `enterprise_readme.md`.
- **Formal Reasoning Parity in `owl_reasoner`**: Axioms `R14`–`R19` use executable OWL 2 RL constructs (`owl:TransitiveProperty`, `rdfs:subPropertyOf`, `owl:someValuesFrom`, `owl:hasValue`, `rdfs:subClassOf`, `owl:disjointWith`) materialized by `owlrl.OWLRL_Semantics`, and policies `R20`–`R28` are executed deterministically by W3C SHACL-SPARQL (`pyshacl`) in `semantics/shapes.ttl`.

## Rule-by-Rule Mapping Matrix

| Rule ID | Domain | `semantics/enterprise_readme.md` | `semantics/ontology.ttl` | `semantics/shapes.ttl` |
|---|---|---|---|---|
| `R01` | `t_acct_mst` schema & enum glossary | Section 1 `[R01]` | `:Account` (`:ruleId "R01"`) | — |
| `R02` | `t_vndr_mst` & `vendor_certs` glossary & non-inheritance | Section 1 `[R02]` | `:Vendor`, `:VendorCertificate` (`:ruleId "R02"`) | — |
| `R03` | `t_ord_hdr` & `t_ord_ln` enums & `disc_pct` | Section 1 `[R03]` | `:SalesOrder` (`:ruleId "R03"`) | — |
| `R04` | `t_rev_sched` GAAP schedule enums | Section 1 `[R04]` | `:RevenueSchedule` (`:ruleId "R04, R08, R09"`) | — |
| `R05` | `t_pmt_tx` positive-refund sign & `fx_rates` lookup | Section 1 `[R05]` | `:PaymentTransaction` (`:ruleId "R05, R10, R11, R12, R13"`) | — |
| `R06` | External Account vs Active External Account | Section 2 `[R06]` | `:ExternalAccount`, `:ActiveExternalAccount` (`:ruleId "R06"`) | — |
| `R07` | Valid Commercial Order & Sales Bookings (USD) | Section 2 `[R07]` | `:ValidCommercialOrder` (`:ruleId "R07"`) | — |
| `R08` | GAAP Recognized Revenue (USD) | Section 2 `[R08]` | `:RevenueSchedule` (`:ruleId "R04, R08, R09"`) | — |
| `R09` | Deferred Revenue Balance (USD) | Section 2 `[R09]` | `:RevenueSchedule` (`:ruleId "R04, R08, R09"`) | — |
| `R10` | Net Settled Cash Collected (USD) | Section 2 `[R10]` | `:PaymentTransaction` (`:ruleId "R05, R10, R11, R12, R13"`) | — |
| `R11` | Gross Refund Rate (%) | Section 2 `[R11]` | `:PaymentTransaction` (`:ruleId "R05, R10, R11, R12, R13"`) | — |
| `R12` | Credit Exposure & Credit Limit Breach | Section 2 `[R12]` | `:PaymentTransaction` (`:ruleId "R05, R10, R11, R12, R13"`) | — |
| `R13` | `t_ord_ln` × `t_pmt_tx` M:N fan-out warning | Section 2 `[R13]` | `:PaymentTransaction` (`:ruleId "R05, R10, R11, R12, R13"`) | — |
| `R14` | Account corporate hierarchy (transitive rollup) | Section 3 `[R14]` | `:hasAncestorAccount` (`owl:TransitiveProperty`), `:directParentAccount` | — |
| `R15` | Transitive Account Sanction inheritance | Section 3 `[R15]` | `:SanctionedAccount` + `owl:hasValue` + `owl:someValuesFrom` axioms | — |
| `R16` | Vendor corporate hierarchy | Section 3 `[R16]` | `:hasAncestorVendor` (`owl:TransitiveProperty`), `:directParentVendor` | — |
| `R17` | Transitive Vendor Sanction/Blacklist inheritance | Section 3 `[R17]` | `:SanctionedVendor` + `owl:hasValue` + `owl:someValuesFrom` axioms | — |
| `R18` | 4-level Product Category taxonomy | Section 3 `[R18]` | `:Product` .. `:ImplantableDevice` `rdfs:subClassOf` tree | — |
| `R19` | Dual-Use & `ExportRegulatedItem` subsumption | Section 3 `[R19]` | `:ExportControlledSoftware` + `:hasDualUseFlag true` -> `:ExportRegulatedItem` ⊑ `:RegulatedProduct` | — |
| `R20` | Sanctioned Vendor PO rejection | Section 4 `[R20]` | `:PurchaseOrderRequest` `:policyRule` | `:RuleR20Shape` (`sh:Violation`) |
| `R21` | On-Hold Vendor PO rejection | Section 4 `[R21]` | `:PurchaseOrderRequest` `:policyRule` | `:RuleR21Shape` (`sh:Violation`) |
| `R22` | MedicalDevice ISO13485 requirement | Section 4 `[R22]` | `:PurchaseOrderRequest` `:policyRule` | `:RuleR22Shape` (`sh:Violation`) |
| `R23a` | RadioactiveIsotope HAZMAT_CLASS7 requirement | Section 4 `[R23a]` | `:PurchaseOrderRequest` `:policyRule` | `:RuleR23aShape` (`sh:Violation`) |
| `R23b` | RadioactiveIsotope air freight ban (disjointness) | Section 4 `[R23b]` | `:RadioactiveIsotope owl:disjointWith :ExpeditedAirEligible` + `:policyRule` | `:RuleR23bShape` (`sh:Violation`) |
| `R24` | CryogenicAgent HAZMAT_CHEM requirement | Section 4 `[R24]` | `:PurchaseOrderRequest` `:policyRule` | `:RuleR24Shape` (`sh:Violation`) |
| `R25` | ExportRegulatedItem EAR_EXPORT requirement | Section 4 `[R25]` | `:PurchaseOrderRequest` `:policyRule` | `:RuleR25Shape` (`sh:Violation`) |
| `R26` | Expedited ImplantableDevice VP escalation | Section 4 `[R26]` | `:PurchaseOrderRequest` `:policyRule` | `:RuleR26Shape` (`sh:Warning`) |
| `R27` | High-value PO approval thresholds | Section 4 `[R27]` | `:PurchaseOrderRequest` `:policyRule` | `:RuleR27Shape` (`sh:Warning`) |
| `R28` | Decision precedence, full rules & non-overridability | Section 4 `[R28]` | `:PurchaseOrderRequest` `:policyRule` | Severity precedence in `owl_engine.py` |

## Engineering Maintenance Comparison Notes

- **Adding/modifying a rule in Arm B (`readme_md`)**: Edit 1 section in `semantics/enterprise_readme.md` (typically 1–4 Markdown lines).
- **Adding/modifying a rule in Arm C2 (`owl_reasoner`)**: Edit OWL TBox/annotation in `semantics/ontology.ttl`, add or modify the corresponding SHACL-SPARQL shape in `semantics/shapes.ttl`, and verify SQLite-to-ABox property mapping in `src/bench/owl_engine.py`.
