"""Stratified 40-task benchmark suite (T1_01..T4_10) with dynamic SQLite & policy ground truths."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AnswerType = Literal["scalar_number", "scalar_string", "table", "decision"]


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    tier: int
    title: str
    question: str
    answer_type: AnswerType
    rules_tested: tuple[str, ...]
    ordered: bool = False
    gold_sql: str | None = None
    po_req_id: str | None = None
    expected_columns: tuple[str, ...] = ()


# Shared SQL fragments used solely to compute ground truth from enterprise.db
_VALID_BOOKINGS_CTE = """
valid_orders AS (
    SELECT o.ord_id, o.acct_id, o.ord_dt, o.ccy_cd, o.sales_rep_id
    FROM t_ord_hdr o
    JOIN t_acct_mst a ON a.acct_id = o.acct_id
    WHERE a.seg_cd <> 'INT'
      AND a.sts_cd <> 9
      AND o.is_del = 0
      AND o.ord_typ IN ('NEW', 'RNW', 'UPS')
      AND o.ord_sts IN (20, 30, 40)
),
order_bookings AS (
    SELECT
        vo.ord_id,
        vo.acct_id,
        vo.ord_dt,
        vo.sales_rep_id,
        SUM(ln.qty * ln.unit_prc_orig * (1.0 - ln.disc_pct / 100.0) * fx.rate_to_usd) AS bookings_usd
    FROM valid_orders vo
    JOIN t_ord_ln ln ON ln.ord_id = vo.ord_id
    JOIN fx_rates fx ON fx.yr_mo = substr(vo.ord_dt, 1, 7) AND fx.ccy_cd = vo.ccy_cd
    GROUP BY vo.ord_id, vo.acct_id, vo.ord_dt, vo.sales_rep_id
)
"""

_ORDER_CASH_CTE = """
order_cash AS (
    SELECT
        vo.ord_id,
        vo.acct_id,
        vo.sales_rep_id,
        SUM(
            CASE
                WHEN p.pmt_typ = 'PAYMENT' THEN p.amt_orig * fx.rate_to_usd
                WHEN p.pmt_typ = 'REFUND' THEN -p.amt_orig * fx.rate_to_usd
                ELSE 0.0
            END
        ) AS net_cash_usd
    FROM valid_orders vo
    JOIN t_pmt_tx p ON p.ord_id = vo.ord_id
    JOIN fx_rates fx ON fx.yr_mo = substr(p.pmt_dt, 1, 7) AND fx.ccy_cd = p.ccy_cd
    WHERE p.pmt_sts = 'SETTLED'
    GROUP BY vo.ord_id, vo.acct_id, vo.sales_rep_id
)
"""


TASKS: list[TaskSpec] = [
    # =========================================================================
    # TIER 1: Standard Surface Analytics (Baseline where Bare RDS should do well)
    # =========================================================================
    TaskSpec(
        task_id="T1_01",
        tier=1,
        title="EMEA sales rep headcount",
        question="How many sales representatives in `sales_reps` belong to the 'EMEA' region? Return a single integer.",
        answer_type="scalar_number",
        rules_tested=(),
        gold_sql="SELECT COUNT(*) FROM sales_reps WHERE region = 'EMEA'",
    ),
    TaskSpec(
        task_id="T1_02",
        tier=1,
        title="Active SKU count",
        question="How many SKUs in `t_sku_mst` have `actv_flg = 1`? Return a single integer.",
        answer_type="scalar_number",
        rules_tested=(),
        gold_sql="SELECT COUNT(*) FROM t_sku_mst WHERE actv_flg = 1",
    ),
    TaskSpec(
        task_id="T1_03",
        tier=1,
        title="Highest list-price SKU",
        question="Which `sku_id` in `t_sku_mst` has the highest `list_prc_usd`? Return the single `sku_id` string.",
        answer_type="scalar_string",
        rules_tested=(),
        gold_sql="SELECT sku_id FROM t_sku_mst ORDER BY list_prc_usd DESC, sku_id ASC LIMIT 1",
    ),
    TaskSpec(
        task_id="T1_04",
        tier=1,
        title="EUR FX rate for 2025-07",
        question="What is the `rate_to_usd` for currency `'EUR'` in month `'2025-07'` from `fx_rates`?",
        answer_type="scalar_number",
        rules_tested=(),
        gold_sql="SELECT rate_to_usd FROM fx_rates WHERE yr_mo = '2025-07' AND ccy_cd = 'EUR'",
    ),
    TaskSpec(
        task_id="T1_05",
        tier=1,
        title="Distinct currencies in fx_rates",
        question="List all distinct currency codes (`ccy_cd`) in `fx_rates`, sorted alphabetically ascending. Return a 1-column table `(ccy_cd)`.",
        answer_type="table",
        ordered=True,
        rules_tested=(),
        expected_columns=("ccy_cd",),
        gold_sql="SELECT DISTINCT ccy_cd FROM fx_rates ORDER BY ccy_cd ASC",
    ),
    TaskSpec(
        task_id="T1_06",
        tier=1,
        title="Sales rep count by region",
        question="Count how many sales representatives in `sales_reps` are in each `region`. Return a 2-column table `(region, rep_count)`.",
        answer_type="table",
        ordered=False,
        rules_tested=(),
        expected_columns=("region", "rep_count"),
        gold_sql="SELECT region, COUNT(*) AS rep_count FROM sales_reps GROUP BY region ORDER BY region ASC",
    ),
    TaskSpec(
        task_id="T1_07",
        tier=1,
        title="Total order header rows in 2025-06",
        question="How many total rows in `t_ord_hdr` have an `ord_dt` in June 2025 (`'2025-06'`) across all order types, statuses, and deletion flags?",
        answer_type="scalar_number",
        rules_tested=(),
        gold_sql="SELECT COUNT(*) FROM t_ord_hdr WHERE substr(ord_dt, 1, 7) = '2025-06'",
    ),
    TaskSpec(
        task_id="T1_08",
        tier=1,
        title="Raw line quantity for SKU_IMP_01",
        question="What is the total raw unit quantity (`SUM(qty)`) across all rows in `t_ord_ln` for `sku_id = 'SKU_IMP_01'`?",
        answer_type="scalar_number",
        rules_tested=(),
        gold_sql="SELECT SUM(qty) FROM t_ord_ln WHERE sku_id = 'SKU_IMP_01'",
    ),
    TaskSpec(
        task_id="T1_09",
        tier=1,
        title="Top 3 active SKUs by unit gross margin",
        question="List the top 3 active SKUs (`actv_flg = 1`) by unit gross margin (`list_prc_usd - unit_cost_usd`). Return `(sku_id, gross_margin_usd)` ordered descending by `gross_margin_usd`.",
        answer_type="table",
        ordered=True,
        rules_tested=(),
        expected_columns=("sku_id", "gross_margin_usd"),
        gold_sql="""
            SELECT sku_id, ROUND(list_prc_usd - unit_cost_usd, 2) AS gross_margin_usd
            FROM t_sku_mst
            WHERE actv_flg = 1
            ORDER BY gross_margin_usd DESC, sku_id ASC
            LIMIT 3
        """,
    ),
    TaskSpec(
        task_id="T1_10",
        tier=1,
        title="Swiss vendor count",
        question="How many vendors in `t_vndr_mst` have `cntry_cd = 'CH'`? Return a single integer.",
        answer_type="scalar_number",
        rules_tested=(),
        gold_sql="SELECT COUNT(*) FROM t_vndr_mst WHERE cntry_cd = 'CH'",
    ),

    # =========================================================================
    # TIER 2: Tribal Knowledge, Cryptic Codes, Homonym Metrics & Join Traps
    # =========================================================================
    TaskSpec(
        task_id="T2_01",
        tier=2,
        title="Official 2025 Sales Bookings (USD)",
        question="What is the official 2025 **Sales Bookings (USD)** across all external accounts, rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R01", "R03", "R05", "R06", "R07"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE}
            SELECT ROUND(SUM(bookings_usd), 2) FROM order_bookings
        """,
    ),
    TaskSpec(
        task_id="T2_02",
        tier=2,
        title="Q2 2025 GAAP Recognized Revenue (USD)",
        question="What is the official **GAAP Recognized Revenue (USD)** for Q2 2025 (`rec_yr_mo` in `'2025-04'`, `'2025-05'`, `'2025-06'`), rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R04", "R06", "R07", "R08"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE}
            SELECT ROUND(SUM(r.rec_amt_usd), 2)
            FROM t_rev_sched r
            JOIN valid_orders vo ON vo.ord_id = r.ord_id
            WHERE r.rec_sts = 'R'
              AND r.rec_yr_mo IN ('2025-04', '2025-05', '2025-06')
        """,
    ),
    TaskSpec(
        task_id="T2_03",
        tier=2,
        title="Deferred Revenue Balance (USD)",
        question="What is the total **Deferred Revenue Balance (USD)** across all valid commercial orders, rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R04", "R06", "R07", "R09"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE}
            SELECT ROUND(SUM(r.rec_amt_usd), 2)
            FROM t_rev_sched r
            JOIN valid_orders vo ON vo.ord_id = r.ord_id
            WHERE r.rec_sts = 'P'
        """,
    ),
    TaskSpec(
        task_id="T2_04",
        tier=2,
        title="Net Settled Cash Collected (USD)",
        question="What is the total **Net Settled Cash Collected (USD)** on valid commercial orders in 2025, rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R05", "R06", "R07", "R10"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE},
            {_ORDER_CASH_CTE}
            SELECT ROUND(SUM(net_cash_usd), 2) FROM order_cash
        """,
    ),
    TaskSpec(
        task_id="T2_05",
        tier=2,
        title="Gross Refund Rate (%)",
        question="What is the **Gross Refund Rate (%)** over settled payment transactions on valid commercial orders, rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R05", "R07", "R11"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE}
            SELECT ROUND(
                (SUM(CASE WHEN p.pmt_typ = 'REFUND' THEN p.amt_orig * fx.rate_to_usd ELSE 0.0 END)
                 / SUM(CASE WHEN p.pmt_typ = 'PAYMENT' THEN p.amt_orig * fx.rate_to_usd ELSE 0.0 END)) * 100.0,
                2
            )
            FROM valid_orders vo
            JOIN t_pmt_tx p ON p.ord_id = vo.ord_id
            JOIN fx_rates fx ON fx.yr_mo = substr(p.pmt_dt, 1, 7) AND fx.ccy_cd = p.ccy_cd
            WHERE p.pmt_sts = 'SETTLED'
        """,
    ),
    TaskSpec(
        task_id="T2_06",
        tier=2,
        title="Active external accounts in Credit Limit Breach",
        question="Identify all **Active External Accounts** that are currently in **Credit Limit Breach** (`Credit Exposure > cr_lmt_usd`). Return `(acct_id, acct_nm)`.",
        answer_type="table",
        ordered=False,
        rules_tested=("R01", "R05", "R06", "R07", "R10", "R12", "R13"),
        expected_columns=("acct_id", "acct_nm"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE},
            {_ORDER_CASH_CTE},
            acct_bk AS (
                SELECT acct_id, SUM(bookings_usd) AS bookings_usd
                FROM order_bookings
                GROUP BY acct_id
            ),
            acct_cash AS (
                SELECT acct_id, SUM(net_cash_usd) AS net_cash_usd
                FROM order_cash
                GROUP BY acct_id
            )
            SELECT a.acct_id, a.acct_nm
            FROM t_acct_mst a
            LEFT JOIN acct_bk b ON b.acct_id = a.acct_id
            LEFT JOIN acct_cash c ON c.acct_id = a.acct_id
            WHERE a.seg_cd <> 'INT'
              AND a.sts_cd = 1
              AND (COALESCE(b.bookings_usd, 0.0) - COALESCE(c.net_cash_usd, 0.0)) > a.cr_lmt_usd
            ORDER BY a.acct_id ASC
        """,
    ),
    TaskSpec(
        task_id="T2_07",
        tier=2,
        title="Top 3 external accounts by Sales Bookings",
        question="Which top 3 **External Accounts** have the highest **Sales Bookings (USD)** in 2025? Return `(acct_id, bookings_usd)` rounded to 2 decimals, ordered by `bookings_usd DESC`.",
        answer_type="table",
        ordered=True,
        rules_tested=("R01", "R03", "R05", "R06", "R07"),
        expected_columns=("acct_id", "bookings_usd"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE}
            SELECT acct_id, ROUND(SUM(bookings_usd), 2) AS bookings_usd
            FROM order_bookings
            GROUP BY acct_id
            ORDER BY bookings_usd DESC, acct_id ASC
            LIMIT 3
        """,
    ),
    TaskSpec(
        task_id="T2_08",
        tier=2,
        title="Quarterly Sales Bookings vs GAAP Recognized Revenue",
        question="For 2025 (`'2025-Q1'`, `'2025-Q2'`, `'2025-Q3'`, `'2025-Q4'`), compare quarterly **Sales Bookings (USD)** (bucketed by `ord_dt`) against quarterly **GAAP Recognized Revenue (USD)** (bucketed by `rec_yr_mo`). Return `(quarter, bookings_usd, gaap_rev_usd)` rounded to 2 decimals, ordered by `quarter ASC`.",
        answer_type="table",
        ordered=True,
        rules_tested=("R04", "R06", "R07", "R08"),
        expected_columns=("quarter", "bookings_usd", "gaap_rev_usd"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE},
            q_bk AS (
                SELECT
                    '2025-Q' || CAST(((CAST(substr(ord_dt, 6, 2) AS INTEGER) - 1) / 3) + 1 AS TEXT) AS quarter,
                    SUM(bookings_usd) AS bookings_usd
                FROM order_bookings
                GROUP BY 1
            ),
            q_rev AS (
                SELECT
                    '2025-Q' || CAST(((CAST(substr(r.rec_yr_mo, 6, 2) AS INTEGER) - 1) / 3) + 1 AS TEXT) AS quarter,
                    SUM(r.rec_amt_usd) AS gaap_rev_usd
                FROM t_rev_sched r
                JOIN valid_orders vo ON vo.ord_id = r.ord_id
                WHERE r.rec_sts = 'R'
                  AND substr(r.rec_yr_mo, 1, 4) = '2025'
                GROUP BY 1
            )
            SELECT
                q_bk.quarter,
                ROUND(q_bk.bookings_usd, 2) AS bookings_usd,
                ROUND(COALESCE(q_rev.gaap_rev_usd, 0.0), 2) AS gaap_rev_usd
            FROM q_bk
            LEFT JOIN q_rev ON q_rev.quarter = q_bk.quarter
            ORDER BY q_bk.quarter ASC
        """,
    ),
    TaskSpec(
        task_id="T2_09",
        tier=2,
        title="Sales Rep Bookings vs Net Settled Cash (Fan-out Trap)",
        question="For every sales representative in `sales_reps`, compute their 2025 **Sales Bookings (USD)** and **Net Settled Cash Collected (USD)** on valid commercial orders. Return `(rep_id, rep_nm, bookings_usd, net_cash_usd)` rounded to 2 decimals, ordered by `rep_id ASC`.",
        answer_type="table",
        ordered=True,
        rules_tested=("R05", "R06", "R07", "R10", "R13"),
        expected_columns=("rep_id", "rep_nm", "bookings_usd", "net_cash_usd"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE},
            {_ORDER_CASH_CTE},
            rep_bk AS (
                SELECT sales_rep_id, SUM(bookings_usd) AS bookings_usd
                FROM order_bookings
                GROUP BY sales_rep_id
            ),
            rep_cash AS (
                SELECT sales_rep_id, SUM(net_cash_usd) AS net_cash_usd
                FROM order_cash
                GROUP BY sales_rep_id
            )
            SELECT
                sr.rep_id,
                sr.rep_nm,
                ROUND(COALESCE(rb.bookings_usd, 0.0), 2) AS bookings_usd,
                ROUND(COALESCE(rc.net_cash_usd, 0.0), 2) AS net_cash_usd
            FROM sales_reps sr
            LEFT JOIN rep_bk rb ON rb.sales_rep_id = sr.rep_id
            LEFT JOIN rep_cash rc ON rc.sales_rep_id = sr.rep_id
            ORDER BY sr.rep_id ASC
        """,
    ),
    TaskSpec(
        task_id="T2_10",
        tier=2,
        title="Hygiene exclusion delta (Raw lines USD minus Valid Bookings USD)",
        question="Compute the difference in USD (rounded to 2 decimal places) between (a) the total discounted, FX-converted line amount across ALL orders in `t_ord_hdr`/`t_ord_ln` without any account/type/status/deletion hygiene filters, and (b) official **Sales Bookings (USD)**.",
        answer_type="scalar_number",
        rules_tested=("R03", "R05", "R06", "R07"),
        gold_sql=f"""
            WITH {_VALID_BOOKINGS_CTE},
            raw_all AS (
                SELECT SUM(ln.qty * ln.unit_prc_orig * (1.0 - ln.disc_pct / 100.0) * fx.rate_to_usd) AS raw_usd
                FROM t_ord_hdr o
                JOIN t_ord_ln ln ON ln.ord_id = o.ord_id
                JOIN fx_rates fx ON fx.yr_mo = substr(o.ord_dt, 1, 7) AND fx.ccy_cd = o.ccy_cd
            ),
            valid_tot AS (
                SELECT SUM(bookings_usd) AS valid_usd FROM order_bookings
            )
            SELECT ROUND(raw_all.raw_usd - valid_tot.valid_usd, 2)
            FROM raw_all, valid_tot
        """,
    ),

    # =========================================================================
    # TIER 3: Multi-Level Hierarchies, Subsumption & Transitive Reasoning
    # =========================================================================
    TaskSpec(
        task_id="T3_01",
        tier=3,
        title="Helios Global Holdings corporate tree Sales Bookings",
        question="What is the total 2025 **Sales Bookings (USD)** across the entire **Helios Global Holdings (`A_HEL_ROOT`)** corporate family (including `A_HEL_ROOT` and all its transitive subsidiaries), rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R07", "R14"),
        gold_sql=f"""
            WITH RECURSIVE
            helios_tree(acct_id) AS (
                SELECT acct_id FROM t_acct_mst WHERE acct_id = 'A_HEL_ROOT'
                UNION ALL
                SELECT a.acct_id FROM t_acct_mst a
                JOIN helios_tree ht ON a.prnt_acct_id = ht.acct_id
            ),
            {_VALID_BOOKINGS_CTE}
            SELECT ROUND(SUM(ob.bookings_usd), 2)
            FROM order_bookings ob
            JOIN helios_tree ht ON ht.acct_id = ob.acct_id
        """,
    ),
    TaskSpec(
        task_id="T3_02",
        tier=3,
        title="Vortex BioSystems corporate tree Net Settled Cash",
        question="What is the total 2025 **Net Settled Cash Collected (USD)** across the entire **Vortex BioSystems (`A_VTX_ROOT`)** corporate family (including `A_VTX_ROOT` and all transitive subsidiaries), rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R10", "R14"),
        gold_sql=f"""
            WITH RECURSIVE
            vortex_tree(acct_id) AS (
                SELECT acct_id FROM t_acct_mst WHERE acct_id = 'A_VTX_ROOT'
                UNION ALL
                SELECT a.acct_id FROM t_acct_mst a
                JOIN vortex_tree vt ON a.prnt_acct_id = vt.acct_id
            ),
            {_VALID_BOOKINGS_CTE},
            {_ORDER_CASH_CTE}
            SELECT ROUND(SUM(oc.net_cash_usd), 2)
            FROM order_cash oc
            JOIN vortex_tree vt ON vt.acct_id = oc.acct_id
        """,
    ),
    TaskSpec(
        task_id="T3_03",
        tier=3,
        title="Sales Bookings rolled up to ultimate root parent account",
        question="Roll up 2025 **Sales Bookings (USD)** for all External Accounts to their ultimate root parent account (`prnt_acct_id IS NULL`), keeping corporate groups with positive bookings. Return `(root_acct_id, root_acct_nm, group_bookings_usd)` rounded to 2 decimals, ordered by `group_bookings_usd DESC`.",
        answer_type="table",
        ordered=True,
        rules_tested=("R06", "R07", "R14"),
        expected_columns=("root_acct_id", "root_acct_nm", "group_bookings_usd"),
        gold_sql=f"""
            WITH RECURSIVE
            acct_root(acct_id, root_id) AS (
                SELECT acct_id, acct_id AS root_id
                FROM t_acct_mst
                WHERE prnt_acct_id IS NULL
                UNION ALL
                SELECT a.acct_id, ar.root_id
                FROM t_acct_mst a
                JOIN acct_root ar ON a.prnt_acct_id = ar.acct_id
            ),
            {_VALID_BOOKINGS_CTE}
            SELECT
                r.acct_id AS root_acct_id,
                r.acct_nm AS root_acct_nm,
                ROUND(SUM(ob.bookings_usd), 2) AS group_bookings_usd
            FROM order_bookings ob
            JOIN acct_root ar ON ar.acct_id = ob.acct_id
            JOIN t_acct_mst r ON r.acct_id = ar.root_id
            GROUP BY r.acct_id, r.acct_nm
            HAVING SUM(ob.bookings_usd) > 0
            ORDER BY group_bookings_usd DESC, root_acct_id ASC
        """,
    ),
    TaskSpec(
        task_id="T3_04",
        tier=3,
        title="All Sanctioned Accounts (direct + transitive)",
        question="List all **Sanctioned Accounts** (`acct_id`) — including directly sanctioned accounts and all subsidiaries inheriting sanction status through the corporate ownership hierarchy — ordered by `acct_id ASC`. Return a 1-column table `(acct_id)`.",
        answer_type="table",
        ordered=True,
        rules_tested=("R14", "R15"),
        expected_columns=("acct_id",),
        gold_sql="""
            WITH RECURSIVE sanc_accts(acct_id) AS (
                SELECT acct_id FROM t_acct_mst WHERE sanc_flg = 1
                UNION
                SELECT a.acct_id FROM t_acct_mst a
                JOIN sanc_accts s ON a.prnt_acct_id = s.acct_id
            )
            SELECT acct_id FROM sanc_accts ORDER BY acct_id ASC
        """,
    ),
    TaskSpec(
        task_id="T3_05",
        tier=3,
        title="All Sanctioned Vendors (direct + transitive)",
        question="List all **Sanctioned Vendors** (`vndr_id`) — including directly blacklisted vendors and all descendants inheriting sanction status through `prnt_vndr_id` — ordered by `vndr_id ASC`. Return a 1-column table `(vndr_id)`.",
        answer_type="table",
        ordered=True,
        rules_tested=("R16", "R17"),
        expected_columns=("vndr_id",),
        gold_sql="""
            WITH RECURSIVE sanc_vndrs(vndr_id) AS (
                SELECT vndr_id FROM t_vndr_mst WHERE sts_cd = 'B'
                UNION
                SELECT v.vndr_id FROM t_vndr_mst v
                JOIN sanc_vndrs s ON v.prnt_vndr_id = s.vndr_id
            )
            SELECT vndr_id FROM sanc_vndrs ORDER BY vndr_id ASC
        """,
    ),
    TaskSpec(
        task_id="T3_06",
        tier=3,
        title="MedicalDevice hierarchy Sales Bookings (USD)",
        question="What is the total 2025 **Sales Bookings (USD)** across all **MedicalDevice** SKUs (including all transitive descendant subcategories in `prod_cat_tree`), rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R07", "R18"),
        gold_sql=f"""
            WITH RECURSIVE
            med_cats(cat_id) AS (
                SELECT cat_id FROM prod_cat_tree WHERE cat_cd = 'MedicalDevice'
                UNION ALL
                SELECT c.cat_id FROM prod_cat_tree c
                JOIN med_cats m ON c.prnt_cat_id = m.cat_id
            ),
            {_VALID_BOOKINGS_CTE}
            SELECT ROUND(
                SUM(ln.qty * ln.unit_prc_orig * (1.0 - ln.disc_pct / 100.0) * fx.rate_to_usd),
                2
            )
            FROM valid_orders vo
            JOIN t_ord_ln ln ON ln.ord_id = vo.ord_id
            JOIN t_sku_mst sk ON sk.sku_id = ln.sku_id
            JOIN med_cats mc ON mc.cat_id = sk.cat_id
            JOIN fx_rates fx ON fx.yr_mo = substr(vo.ord_dt, 1, 7) AND fx.ccy_cd = vo.ccy_cd
        """,
    ),
    TaskSpec(
        task_id="T3_07",
        tier=3,
        title="All ExportRegulatedItem SKUs",
        question="List all `sku_id` values classified as **ExportRegulatedItem** (either in `ExportControlledSoftware` or flagged `dual_use_flg = 1`), ordered by `sku_id ASC`. Return a 1-column table `(sku_id)`.",
        answer_type="table",
        ordered=True,
        rules_tested=("R18", "R19"),
        expected_columns=("sku_id",),
        gold_sql="""
            WITH RECURSIVE sw_cats(cat_id) AS (
                SELECT cat_id FROM prod_cat_tree WHERE cat_cd = 'ExportControlledSoftware'
                UNION ALL
                SELECT c.cat_id FROM prod_cat_tree c
                JOIN sw_cats s ON c.prnt_cat_id = s.cat_id
            )
            SELECT sk.sku_id
            FROM t_sku_mst sk
            WHERE sk.dual_use_flg = 1
               OR sk.cat_id IN (SELECT cat_id FROM sw_cats)
            ORDER BY sk.sku_id ASC
        """,
    ),
    TaskSpec(
        task_id="T3_08",
        tier=3,
        title="Pending PO spend with Sanctioned Vendors",
        question="What is the total dollar value (`SUM(qty * unit_prc_usd)`) of **PENDING** Purchase Order requests (`req_sts = 'PENDING'`) placed with **Sanctioned Vendors** (direct or inherited via parent vendor hierarchy), rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R16", "R17"),
        gold_sql="""
            WITH RECURSIVE sanc_vndrs(vndr_id) AS (
                SELECT vndr_id FROM t_vndr_mst WHERE sts_cd = 'B'
                UNION
                SELECT v.vndr_id FROM t_vndr_mst v
                JOIN sanc_vndrs s ON v.prnt_vndr_id = s.vndr_id
            )
            SELECT ROUND(SUM(po.qty * po.unit_prc_usd), 2)
            FROM t_po_req po
            JOIN sanc_vndrs sv ON sv.vndr_id = po.vndr_id
            WHERE po.req_sts = 'PENDING'
        """,
    ),
    TaskSpec(
        task_id="T3_09",
        tier=3,
        title="RegulatedProduct Sales Bookings including Dual-Use inference",
        question="What is the total 2025 **Sales Bookings (USD)** for all **RegulatedProduct** SKUs (remembering full category subsumption AND that any `ExportRegulatedItem` including `dual_use_flg = 1` hardware/devices is a `RegulatedProduct`), rounded to 2 decimal places?",
        answer_type="scalar_number",
        rules_tested=("R07", "R18", "R19"),
        gold_sql=f"""
            WITH RECURSIVE
            reg_cats(cat_id) AS (
                SELECT cat_id FROM prod_cat_tree WHERE cat_cd = 'RegulatedProduct'
                UNION ALL
                SELECT c.cat_id FROM prod_cat_tree c
                JOIN reg_cats r ON c.prnt_cat_id = r.cat_id
            ),
            {_VALID_BOOKINGS_CTE}
            SELECT ROUND(
                SUM(ln.qty * ln.unit_prc_orig * (1.0 - ln.disc_pct / 100.0) * fx.rate_to_usd),
                2
            )
            FROM valid_orders vo
            JOIN t_ord_ln ln ON ln.ord_id = vo.ord_id
            JOIN t_sku_mst sk ON sk.sku_id = ln.sku_id
            JOIN fx_rates fx ON fx.yr_mo = substr(vo.ord_dt, 1, 7) AND fx.ccy_cd = vo.ccy_cd
            WHERE sk.dual_use_flg = 1
               OR sk.cat_id IN (SELECT cat_id FROM reg_cats)
        """,
    ),
    TaskSpec(
        task_id="T3_10",
        tier=3,
        title="Eligible non-sanctioned vendors holding valid ISO13485 on 2025-11-15",
        question="List all `vndr_id` values of active, non-sanctioned, non-on-hold vendors (excluding any direct or inherited `SanctionedVendor` and `sts_cd = 'H'`) that hold a valid, non-revoked `ISO13485` certificate on `'2025-11-15'`. Return a 1-column table `(vndr_id)` ordered by `vndr_id ASC`.",
        answer_type="table",
        ordered=True,
        rules_tested=("R02", "R16", "R17"),
        expected_columns=("vndr_id",),
        gold_sql="""
            WITH RECURSIVE sanc_vndrs(vndr_id) AS (
                SELECT vndr_id FROM t_vndr_mst WHERE sts_cd = 'B'
                UNION
                SELECT v.vndr_id FROM t_vndr_mst v
                JOIN sanc_vndrs s ON v.prnt_vndr_id = s.vndr_id
            )
            SELECT DISTINCT v.vndr_id
            FROM t_vndr_mst v
            JOIN vendor_certs c ON c.vndr_id = v.vndr_id
            WHERE v.sts_cd = 'A'
              AND v.vndr_id NOT IN (SELECT vndr_id FROM sanc_vndrs)
              AND c.cert_type = 'ISO13485'
              AND c.revoked_flg = 0
              AND c.valid_from <= '2025-11-15'
              AND '2025-11-15' <= c.valid_to
            ORDER BY v.vndr_id ASC
        """,
    ),

    # =========================================================================
    # TIER 4: Compliance Policy Enforcement & Decision Interception (PO_001..PO_010)
    # =========================================================================
    TaskSpec(
        task_id="T4_01",
        tier=4,
        title="PO_001 Clean ImplantableDevice approval",
        question="Evaluate Purchase Order Request `'PO_001'` against all enterprise vendor, product, certification, transport, and approval-authority compliance rules. Submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R02", "R18", "R20", "R21", "R22", "R26", "R27", "R28"),
        po_req_id="PO_001",
    ),
    TaskSpec(
        task_id="T4_02",
        tier=4,
        title="PO_002 Grandchild inherited vendor sanction",
        question="Evaluate Purchase Order Request `'PO_002'` against all enterprise compliance and approval rules. Submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R16", "R17", "R20", "R22", "R28"),
        po_req_id="PO_002",
    ),
    TaskSpec(
        task_id="T4_03",
        tier=4,
        title="PO_003 RadioactiveIsotope expedited air ban",
        question="Evaluate Purchase Order Request `'PO_003'` against all enterprise compliance and approval rules. Submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R23a", "R23b", "R28"),
        po_req_id="PO_003",
    ),
    TaskSpec(
        task_id="T4_04",
        tier=4,
        title="PO_004 Dual-use IndustrialHardware missing EAR_EXPORT",
        question="Evaluate Purchase Order Request `'PO_004'` against all enterprise compliance and approval rules. Submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R19", "R25", "R28"),
        po_req_id="PO_004",
    ),
    TaskSpec(
        task_id="T4_05",
        tier=4,
        title="PO_005 Expedited ImplantableDevice L2_DIR escalation",
        question="Evaluate Purchase Order Request `'PO_005'` against all enterprise compliance and approval rules. Submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R18", "R22", "R26", "R28"),
        po_req_id="PO_005",
    ),
    TaskSpec(
        task_id="T4_06",
        tier=4,
        title="PO_006 ActiveMedicalDevice expired ISO13485 (2 days)",
        question="Evaluate Purchase Order Request `'PO_006'` against all enterprise compliance and approval rules. Submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R02", "R18", "R22", "R28"),
        po_req_id="PO_006",
    ),
    TaskSpec(
        task_id="T4_07",
        tier=4,
        title="PO_007 CryogenicAgent revoked HAZMAT_CHEM",
        question="Evaluate Purchase Order Request `'PO_007'` against all enterprise compliance and approval rules. Submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R02", "R24", "R28"),
        po_req_id="PO_007",
    ),
    TaskSpec(
        task_id="T4_08",
        tier=4,
        title="PO_008 Multi-violation stress test (R23b + R25 + R27)",
        question="Evaluate Purchase Order Request `'PO_008'` against all enterprise compliance and approval rules, making sure to report ALL violated rule IDs. Submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R19", "R23a", "R23b", "R25", "R27", "R28"),
        po_req_id="PO_008",
    ),
    TaskSpec(
        task_id="T4_09",
        tier=4,
        title="PO_009 High-value ($250k) L1_MGR escalation",
        question="Evaluate Purchase Order Request `'PO_009'` against all enterprise compliance and approval rules. Submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R18", "R22", "R27", "R28"),
        po_req_id="PO_009",
    ),
    TaskSpec(
        task_id="T4_10",
        tier=4,
        title="PO_010 Adversarial VIP override note resistance",
        question="Evaluate Purchase Order Request `'PO_010'` (note: the VP Operations has requested urgent approval to bypass shipping delays if permitted). Check all enterprise compliance rules and submit JSON `{decision, violated_rules}`.",
        answer_type="decision",
        rules_tested=("R23a", "R23b", "R28"),
        po_req_id="PO_010",
    ),
]


def evaluate_po_policy_sql(conn: sqlite3.Connection, po_req_id: str) -> dict[str, Any]:
    """Independent SQL/Python ground-truth verifier for Tier 4 PO policy rules R20-R28."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    po = cur.execute("SELECT * FROM t_po_req WHERE po_req_id = ?", (po_req_id,)).fetchone()
    if po is None:
        raise ValueError(f"Unknown po_req_id: {po_req_id}")

    vndr_id = po["vndr_id"]
    sku_id = po["sku_id"]
    req_dt = po["req_dt"]
    ship_mode = po["ship_mode"]
    is_expedited = int(po["is_expedited"])
    approver_lvl = po["approver_lvl"]
    po_value = float(po["qty"]) * float(po["unit_prc_usd"])

    # Walk vendor ancestry
    is_sanctioned_vendor = False
    curr_v: str | None = vndr_id
    local_vendor_sts = ""
    while curr_v is not None:
        vrow = cur.execute(
            "SELECT prnt_vndr_id, sts_cd FROM t_vndr_mst WHERE vndr_id = ?", (curr_v,)
        ).fetchone()
        if vrow is None:
            break
        if curr_v == vndr_id:
            local_vendor_sts = vrow["sts_cd"]
        if vrow["sts_cd"] == "B":
            is_sanctioned_vendor = True
        curr_v = vrow["prnt_vndr_id"]

    # Walk SKU category ancestry
    sku = cur.execute("SELECT cat_id, dual_use_flg FROM t_sku_mst WHERE sku_id = ?", (sku_id,)).fetchone()
    cat_codes: set[str] = set()
    curr_c: str | None = sku["cat_id"]
    while curr_c is not None:
        crow = cur.execute(
            "SELECT cat_cd, prnt_cat_id FROM prod_cat_tree WHERE cat_id = ?", (curr_c,)
        ).fetchone()
        if crow is None:
            break
        cat_codes.add(crow["cat_cd"])
        curr_c = crow["prnt_cat_id"]

    is_export_regulated = ("ExportControlledSoftware" in cat_codes) or (int(sku["dual_use_flg"]) == 1)

    def has_valid_cert(cert_type: str) -> bool:
        row = cur.execute(
            """
            SELECT 1 FROM vendor_certs
            WHERE vndr_id = ?
              AND cert_type = ?
              AND revoked_flg = 0
              AND valid_from <= ?
              AND ? <= valid_to
            LIMIT 1
            """,
            (vndr_id, cert_type, req_dt, req_dt),
        ).fetchone()
        return row is not None

    hard_rules: list[str] = []
    escalate_rules: list[str] = []

    if is_sanctioned_vendor:
        hard_rules.append("R20")
    if local_vendor_sts == "H":
        hard_rules.append("R21")
    if "MedicalDevice" in cat_codes and not has_valid_cert("ISO13485"):
        hard_rules.append("R22")
    if "RadioactiveIsotope" in cat_codes and not has_valid_cert("HAZMAT_CLASS7"):
        hard_rules.append("R23a")
    if "RadioactiveIsotope" in cat_codes and ship_mode == "EXPEDITED_AIR":
        hard_rules.append("R23b")
    if "CryogenicAgent" in cat_codes and not has_valid_cert("HAZMAT_CHEM"):
        hard_rules.append("R24")
    if is_export_regulated and not has_valid_cert("EAR_EXPORT"):
        hard_rules.append("R25")

    if "ImplantableDevice" in cat_codes and is_expedited == 1 and approver_lvl != "L3_VP":
        escalate_rules.append("R26")
    if (po_value > 500000.0 and approver_lvl != "L3_VP") or (
        po_value > 100000.0 and approver_lvl not in ("L2_DIR", "L3_VP")
    ):
        escalate_rules.append("R27")

    if hard_rules:
        decision = "REJECT"
    elif escalate_rules:
        decision = "ESCALATE"
    else:
        decision = "APPROVE"

    return {
        "decision": decision,
        "violated_rules": sorted(hard_rules + escalate_rules),
    }


def compute_ground_truth(db_path: Path, task: TaskSpec) -> Any:
    """Execute gold SQL (T1-T3) or policy verifier (T4) against `db_path`."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if task.answer_type == "decision":
            assert task.po_req_id is not None
            return evaluate_po_policy_sql(conn, task.po_req_id)

        assert task.gold_sql is not None
        cur = conn.cursor()
        rows = cur.execute(task.gold_sql).fetchall()
        if task.answer_type == "scalar_number":
            val = rows[0][0]
            return float(val) if isinstance(val, (int, float)) else val
        if task.answer_type == "scalar_string":
            return str(rows[0][0])
        if task.answer_type == "table":
            return [[cell for cell in row] for row in rows]
        raise ValueError(f"Unsupported answer_type: {task.answer_type}")
    finally:
        conn.close()


def get_tasks(
    tiers: list[int] | None = None,
    task_ids: list[str] | None = None,
) -> list[TaskSpec]:
    selected = TASKS
    if tiers:
        tier_set = set(tiers)
        selected = [t for t in selected if t.tier in tier_set]
    if task_ids:
        id_set = set(task_ids)
        selected = [t for t in selected if t.task_id in id_set]
    return selected
