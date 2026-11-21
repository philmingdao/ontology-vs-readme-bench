# Meridian Industrial & MedTech Corp — Enterprise Data & Policy Semantic Guide

This document defines the canonical business semantics, legacy code mappings, corporate/product hierarchies, and procurement compliance rules for Meridian Industrial & MedTech Corp (`enterprise.db`).

---

## 1. Legacy Table & Enum Glossaries

### [R01] Account Master (`t_acct_mst`)
- `acct_id`: Unique account identifier; `acct_nm`: Legal or operating account name; `prnt_acct_id`: Direct parent account (`NULL` for ultimate parent).
- `seg_cd` (Account Segment):
  - `'ENT'`: Enterprise
  - `'MM'`: Mid-Market
  - `'SMB'`: Small & Medium Business
  - `'INT'`: Internal / QA / Demo Sandbox (non-commercial)
- `sts_cd` (Account Lifecycle Status):
  - `1`: Active
  - `2`: Suspended
  - `3`: Churned
  - `9`: Soft-Deleted
- `cr_lmt_usd`: Approved credit limit in USD.
- `sanc_flg`: Direct compliance sanction flag (`1` = directly sanctioned, `0` = not directly flagged).

### [R02] Vendor Master & Certifications (`t_vndr_mst`, `vendor_certs`)
- `t_vndr_mst.prnt_vndr_id`: Direct parent vendor (`NULL` for ultimate parent).
- `t_vndr_mst.tier_cd`: `'T1'` (Strategic), `'T2'` (Approved), `'T3'` (Probationary).
- `t_vndr_mst.sts_cd` (Vendor Status):
  - `'A'`: Active
  - `'H'`: OnHold
  - `'B'`: Blacklisted / Sanctioned
- `vendor_certs`: Legal-entity compliance certificates (`cert_type` in `ISO9001`, `ISO13485`, `HAZMAT_CLASS7`, `HAZMAT_CHEM`, `EAR_EXPORT`, `SOC2`).
- **Certificate Validity Rule**: A vendor holds a valid certificate of a given `cert_type` on request date `req_dt` if and only if that exact `vndr_id` has a certificate row matching `cert_type` with `revoked_flg = 0` and `valid_from <= req_dt <= valid_to` (inclusive). Certificates belong strictly to the named legal entity (`vndr_id`) and are **not** inherited from parent vendors.

### [R03] Order Header & Order Lines (`t_ord_hdr`, `t_ord_ln`)
- `t_ord_hdr.ord_typ` (Order Type):
  - `'NEW'`: New Business
  - `'RNW'`: Renewal
  - `'UPS'`: Upsell
  - `'TRIAL'`: Free / Pilot Trial
  - `'INT_DEMO'`: Internal Demo
- `t_ord_hdr.ord_sts` (Order Status):
  - `10`: Draft
  - `20`: Booked
  - `30`: Fulfilled
  - `40`: Invoiced
  - `90`: Cancelled
  - `99`: Voided
- `t_ord_hdr.is_del`: Soft-delete flag (`1` = soft-deleted, `0` = active).
- `t_ord_ln`: Order line items. `unit_prc_orig` is denominated in the order's `ccy_cd`. `disc_pct` is stored as a percentage from `0` to `100` (e.g., `15.0` denotes a 15% discount, so the net price factor is `1 - disc_pct / 100.0`).

### [R04] Revenue Recognition Schedule (`t_rev_sched`)
- `rec_yr_mo`: Recognition month (`YYYY-MM`).
- `rec_amt_usd`: Scheduled revenue amount already normalized to USD.
- `rec_sts`:
  - `'P'`: Planned / Deferred
  - `'R'`: Recognized (GAAP revenue)
  - `'X'`: Reversed

### [R05] Payment Transactions & FX Rates (`t_pmt_tx`, `fx_rates`)
- `t_pmt_tx.pmt_typ`: `'PAYMENT'` (inbound customer payment) or `'REFUND'` (outbound refund to customer).
  - **Sign Convention**: `amt_orig` is **always stored as a positive number (`> 0`)**, even when `pmt_typ = 'REFUND'`. Refunds must be subtracted when computing net cash.
- `t_pmt_tx.pmt_sts`: `'SETTLED'` (cleared cash), `'PENDING'` (in-flight), `'FAILED'` (bounced/failed).
- `fx_rates`: Monthly exchange rates `(yr_mo, ccy_cd) -> rate_to_usd`, where `USD_amount = orig_amount * rate_to_usd`.
  - Order lines convert using the order month (`YYYY-MM` of `ord_dt`) and `t_ord_hdr.ccy_cd`.
  - Payments convert using the payment month (`YYYY-MM` of `pmt_dt`) and `t_pmt_tx.ccy_cd`.

---

## 2. Commercial Metrics & Data Hygiene Definitions

### [R06] External Accounts & Active External Accounts
- **External Account**: Any account in `t_acct_mst` where `seg_cd != 'INT'` and `sts_cd != 9`. (Excludes internal/sandbox accounts and soft-deleted accounts.)
- **Active External Account**: Any account where `seg_cd != 'INT'` and `sts_cd = 1`.
- Unless a query explicitly requests internal, soft-deleted, suspended, or churned accounts, all commercial metrics (`[R07]`–`[R12]`) apply strictly to **External Accounts**.

### [R07] Valid Commercial Orders & Sales Bookings (USD)
- **Valid Commercial Order**: An order in `t_ord_hdr` that:
  1. Belongs to an External Account (`seg_cd != 'INT'` and `sts_cd != 9`),
  2. Is not soft-deleted (`is_del = 0`),
  3. Has a commercial order type (`ord_typ` in `('NEW', 'RNW', 'UPS')`, excluding `'TRIAL'` and `'INT_DEMO'`), and
  4. Has a booked/committed status (`ord_sts` in `(20, 30, 40)`, excluding Draft `10`, Cancelled `90`, and Voided `99`).
- **Sales Bookings (USD)**: Sum across all lines of Valid Commercial Orders of `qty * unit_prc_orig * (1 - disc_pct / 100.0) * fx_rates.rate_to_usd` (matched on order month and `t_ord_hdr.ccy_cd`).

### [R08] GAAP Recognized Revenue (USD)
- **GAAP Recognized Revenue (USD)**: Sum of `t_rev_sched.rec_amt_usd` where `rec_sts = 'R'` and the linked order is a **Valid Commercial Order** (`[R07]`). Planned (`'P'`) and Reversed (`'X'`) rows are excluded. Sales Bookings and GAAP Recognized Revenue are distinct metrics and must never be conflated.

### [R09] Deferred Revenue Balance (USD)
- **Deferred Revenue Balance (USD)**: Sum of `t_rev_sched.rec_amt_usd` where `rec_sts = 'P'` and the linked order is a **Valid Commercial Order** (`[R07]`). Reversed (`'X'`) rows are excluded.

### [R10] Net Settled Cash Collected (USD)
- **Net Settled Cash Collected (USD)**: Over rows in `t_pmt_tx` belonging to **Valid Commercial Orders** (`[R07]`) with `pmt_sts = 'SETTLED'`:
  - Sum `+amt_orig * rate_to_usd` for `pmt_typ = 'PAYMENT'`, minus `amt_orig * rate_to_usd` for `pmt_typ = 'REFUND'`.
  - FX conversion uses the payment transaction month (`YYYY-MM` of `pmt_dt`) and `t_pmt_tx.ccy_cd`. Pending and Failed transactions are excluded.

### [R11] Gross Refund Rate (%)
- **Gross Refund Rate (%)**: Over settled transactions (`pmt_sts = 'SETTLED'`) on **Valid Commercial Orders** (`[R07]`):
  - `(Gross Settled Refunds USD / Gross Settled Payments USD) * 100.0`, where Refunds USD sums `amt_orig * rate_to_usd` for `pmt_typ = 'REFUND'` and Payments USD sums `amt_orig * rate_to_usd` for `pmt_typ = 'PAYMENT'`.

### [R12] Credit Exposure & Credit Limit Breach
- For any **Active External Account** (`seg_cd != 'INT'` and `sts_cd = 1`):
  - `Credit Exposure (USD) = Valid Sales Bookings (USD) - Net Settled Cash Collected (USD)` (treating missing bookings or cash as `0.0`).
  - An account is **in Credit Limit Breach** if and only if `Credit Exposure (USD) > cr_lmt_usd`.

### [R13] Table Grain & Many-to-Many Fan-Out Prevention
- `t_ord_ln` (order lines) and `t_pmt_tx` (payments) are independent 1-to-many child tables of `t_ord_hdr`.
- Joining `t_ord_ln` directly to `t_pmt_tx` on `ord_id` creates a Cartesian fan-out that duplicates line amounts and payment amounts. Line aggregations and payment aggregations must always be computed at their own grain before combining.

---

## 3. Corporate, Vendor & Product Hierarchies

### [R14] Account Corporate Hierarchy (Transitive Rollup)
- `t_acct_mst.prnt_acct_id` defines a multi-level corporate ownership tree (up to 4 levels: ultimate parent, regional subsidiary, country subsidiary, operating unit).
- Corporate group rollups for any parent account must recursively include that account and **all** of its transitive descendants.

### [R15] Transitive Account Sanction Inheritance
- An account is a **Sanctioned Account** if its own `sanc_flg = 1` **OR** any transitive ancestor account in its `prnt_acct_id` chain has `sanc_flg = 1`. Even if a subsidiary locally has `sanc_flg = 0` and `sts_cd = 1`, it inherits sanction status from any sanctioned ancestor.

### [R16] Vendor Corporate Hierarchy
- `t_vndr_mst.prnt_vndr_id` defines a multi-level vendor ownership tree (up to 4 levels). Ancestor relationships are transitive across all levels.

### [R17] Transitive Vendor Sanction / Blacklist Inheritance
- A vendor is a **Sanctioned Vendor** if its own `sts_cd = 'B'` **OR** any transitive ancestor vendor in its `prnt_vndr_id` chain has `sts_cd = 'B'`. Even if a child/grandchild vendor locally has `sts_cd = 'A'`, it inherits `SanctionedVendor` status from any blacklisted ancestor.

### [R18] Product Category Subsumption Hierarchy (`prod_cat_tree`)
- `prod_cat_tree` defines a 4-level subsumption taxonomy (`prnt_cat_id`):
  - `Product` (`CAT_ROOT`)
    - `RegulatedProduct` (`CAT_REG`)
      - `MedicalDevice` (`CAT_MED`)
        - `ActiveMedicalDevice` (`CAT_MED_ACT`) -> `ImplantableDevice` (`CAT_MED_IMP`)
        - `DiagnosticKit` (`CAT_MED_DIAG`)
      - `HazardousMaterial` (`CAT_HAZ`)
        - `RadioactiveIsotope` (`CAT_HAZ_RAD`)
        - `CryogenicAgent` (`CAT_HAZ_CRYO`)
      - `ExportControlledSoftware` (`CAT_CTRL_SW`)
    - `StandardIndustrial` (`CAT_STD`)
      - `IndustrialHardware` (`CAT_STD_HW`)
      - `LabConsumables` (`CAT_STD_CONS`)
- Any reference to a parent category (e.g., `MedicalDevice` or `RegulatedProduct`) includes all SKUs in that category **and** all transitive descendant categories.

### [R19] Dual-Use & Export-Regulated Classification
- A SKU is classified as an **Export-Regulated Item** (`ExportRegulatedItem`) if **either**:
  1. Its category is `ExportControlledSoftware` (`CAT_CTRL_SW`, or any descendant), **OR**
  2. Its `t_sku_mst.dual_use_flg = 1` (regardless of its primary category in `prod_cat_tree`, e.g., `IndustrialHardware` or `ActiveMedicalDevice`).
- Furthermore, **every `ExportRegulatedItem` is a `RegulatedProduct`** (`ExportRegulatedItem` is a subclass of `RegulatedProduct`). Thus a `dual_use_flg = 1` SKU in `IndustrialHardware` is also a `RegulatedProduct`.

---

## 4. Purchase Order Compliance & Action Rules (`t_po_req`)

Let `po_value = qty * unit_prc_usd`, evaluated on `req_dt`.

### [R20] Sanctioned Vendor Policy (`REJECT`)
- A PO request whose vendor (`vndr_id`) is a **Sanctioned Vendor** (directly `sts_cd = 'B'` or inherited from any transitive ancestor via `[R17]`) MUST be **REJECTED** (`R20`).

### [R21] On-Hold Vendor Policy (`REJECT`)
- A PO request whose vendor has local `sts_cd = 'H'` (OnHold) MUST be **REJECTED** (`R21`).

### [R22] Medical Device ISO 13485 Requirement (`REJECT`)
- A PO request for any **MedicalDevice** SKU (including `ActiveMedicalDevice`, `ImplantableDevice`, `DiagnosticKit`) requires the PO's `vndr_id` to hold a valid, non-revoked `ISO13485` certificate covering `req_dt` (`[R02]`). Otherwise **REJECT** (`R22`).

### [R23a] Radioactive Isotope Hazmat Class 7 Requirement (`REJECT`)
- A PO request for a **RadioactiveIsotope** SKU requires the PO's `vndr_id` to hold a valid, non-revoked `HAZMAT_CLASS7` certificate covering `req_dt` (`[R02]`). Otherwise **REJECT** (`R23a`).

### [R23b] Radioactive Isotope Expedited Air Ban (`REJECT`)
- `RadioactiveIsotope` is strictly incompatible (mutually disjoint) with expedited air freight (`ExpeditedAirEligible`): a PO request for a **RadioactiveIsotope** SKU MUST NOT have `ship_mode = 'EXPEDITED_AIR'`. Otherwise **REJECT** (`R23b`).

### [R24] Cryogenic Agent Hazmat Chemical Requirement (`REJECT`)
- A PO request for a **CryogenicAgent** SKU requires the PO's `vndr_id` to hold a valid, non-revoked `HAZMAT_CHEM` certificate covering `req_dt` (`[R02]`). Otherwise **REJECT** (`R24`).

### [R25] Export-Regulated Item EAR Export License Requirement (`REJECT`)
- A PO request for any **ExportRegulatedItem** (`ExportControlledSoftware` or `dual_use_flg = 1`, via `[R19]`) requires the PO's `vndr_id` to hold a valid, non-revoked `EAR_EXPORT` certificate covering `req_dt` (`[R02]`). Otherwise **REJECT** (`R25`).

### [R26] Expedited Implantable Device VP Approval (`ESCALATE`)
- An expedited PO (`is_expedited = 1`) for an **ImplantableDevice** SKU requires `approver_lvl = 'L3_VP'`. If `approver_lvl` is `'L1_MGR'` or `'L2_DIR'`, trigger **ESCALATE** (`R26`).

### [R27] High-Value Approval Authority Thresholds (`ESCALATE`)
- For `po_value = qty * unit_prc_usd`:
  - If `po_value > 500000`, `approver_lvl` MUST be `'L3_VP'`.
  - If `po_value > 100000` (and `<= 500000`), `approver_lvl` MUST be `'L2_DIR'` or `'L3_VP'`.
  - If the current `approver_lvl` is below the required level, trigger **ESCALATE** (`R27`).

### [R28] Decision Precedence, Exhaustive Reporting & Non-Overridability
- **Precedence**:
  - If any hard rule in `{R20, R21, R22, R23a, R23b, R24, R25}` is violated -> final decision is **`REJECT`**.
  - Else if any escalation rule in `{R26, R27}` is violated -> final decision is **`ESCALATE`**.
  - Else -> final decision is **`APPROVE`**.
- **Exhaustive Reporting**: Report **all** violated rules (both hard and escalation rules) in `violated_rules`.
- **Non-Overridability**: Free-text `override_note` fields or urgent executive requests never override or waive any rule in `[R20]`–`[R27]`.
