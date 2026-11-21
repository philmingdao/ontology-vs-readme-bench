"""Deterministic synthetic enterprise database builder for Meridian Industrial & MedTech Corp.

Creates a realistic messy SQLite enterprise database (`enterprise.db`) featuring:
- Legacy abbreviated table/column names and numeric/char status codes
- Homonym commercial metrics (Sales Bookings vs GAAP Recognized Revenue vs Net Settled Cash)
- Positive-stored refunds and multi-currency monthly FX conversions
- Many-to-many order-line vs payment fan-out join trap
- 4-level corporate account, vendor, and product category hierarchies
- Transitive sanction propagation and dual-use item classification
- 15 deterministic Purchase Order requests for compliance & policy evaluation
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE sales_reps (
    rep_id TEXT PRIMARY KEY,
    rep_nm TEXT NOT NULL,
    region TEXT NOT NULL,
    hire_dt TEXT NOT NULL
);

CREATE TABLE t_acct_mst (
    acct_id TEXT PRIMARY KEY,
    acct_nm TEXT NOT NULL,
    prnt_acct_id TEXT REFERENCES t_acct_mst(acct_id),
    seg_cd TEXT NOT NULL CHECK (seg_cd IN ('ENT', 'MM', 'SMB', 'INT')),
    cntry_cd TEXT NOT NULL,
    sts_cd INTEGER NOT NULL CHECK (sts_cd IN (1, 2, 3, 9)),
    cr_lmt_usd REAL NOT NULL,
    sanc_flg INTEGER NOT NULL CHECK (sanc_flg IN (0, 1)),
    crtd_dt TEXT NOT NULL
);

CREATE TABLE t_vndr_mst (
    vndr_id TEXT PRIMARY KEY,
    vndr_nm TEXT NOT NULL,
    prnt_vndr_id TEXT REFERENCES t_vndr_mst(vndr_id),
    cntry_cd TEXT NOT NULL,
    tier_cd TEXT NOT NULL CHECK (tier_cd IN ('T1', 'T2', 'T3')),
    sts_cd TEXT NOT NULL CHECK (sts_cd IN ('A', 'H', 'B')),
    lead_days INTEGER NOT NULL
);

CREATE TABLE vendor_certs (
    cert_id TEXT PRIMARY KEY,
    vndr_id TEXT NOT NULL REFERENCES t_vndr_mst(vndr_id),
    cert_type TEXT NOT NULL CHECK (
        cert_type IN ('ISO9001', 'ISO13485', 'HAZMAT_CLASS7', 'HAZMAT_CHEM', 'EAR_EXPORT', 'SOC2')
    ),
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    revoked_flg INTEGER NOT NULL CHECK (revoked_flg IN (0, 1))
);

CREATE TABLE prod_cat_tree (
    cat_id TEXT PRIMARY KEY,
    cat_cd TEXT NOT NULL UNIQUE,
    cat_nm TEXT NOT NULL,
    prnt_cat_id TEXT REFERENCES prod_cat_tree(cat_id)
);

CREATE TABLE t_sku_mst (
    sku_id TEXT PRIMARY KEY,
    sku_cd TEXT NOT NULL UNIQUE,
    sku_nm TEXT NOT NULL,
    cat_id TEXT NOT NULL REFERENCES prod_cat_tree(cat_id),
    unit_cost_usd REAL NOT NULL,
    list_prc_usd REAL NOT NULL,
    dual_use_flg INTEGER NOT NULL CHECK (dual_use_flg IN (0, 1)),
    actv_flg INTEGER NOT NULL CHECK (actv_flg IN (0, 1))
);

CREATE TABLE fx_rates (
    yr_mo TEXT NOT NULL,
    ccy_cd TEXT NOT NULL,
    rate_to_usd REAL NOT NULL,
    PRIMARY KEY (yr_mo, ccy_cd)
);

CREATE TABLE t_ord_hdr (
    ord_id TEXT PRIMARY KEY,
    acct_id TEXT NOT NULL REFERENCES t_acct_mst(acct_id),
    ord_dt TEXT NOT NULL,
    ccy_cd TEXT NOT NULL,
    ord_typ TEXT NOT NULL CHECK (ord_typ IN ('NEW', 'RNW', 'UPS', 'TRIAL', 'INT_DEMO')),
    ord_sts INTEGER NOT NULL CHECK (ord_sts IN (10, 20, 30, 40, 90, 99)),
    dlv_dt TEXT,
    is_del INTEGER NOT NULL CHECK (is_del IN (0, 1)),
    sales_rep_id TEXT NOT NULL REFERENCES sales_reps(rep_id)
);

CREATE TABLE t_ord_ln (
    ord_ln_id TEXT PRIMARY KEY,
    ord_id TEXT NOT NULL REFERENCES t_ord_hdr(ord_id),
    sku_id TEXT NOT NULL REFERENCES t_sku_mst(sku_id),
    qty INTEGER NOT NULL,
    unit_prc_orig REAL NOT NULL,
    disc_pct REAL NOT NULL,
    svc_start_dt TEXT NOT NULL,
    svc_end_dt TEXT NOT NULL
);

CREATE TABLE t_rev_sched (
    sched_id TEXT PRIMARY KEY,
    ord_id TEXT NOT NULL REFERENCES t_ord_hdr(ord_id),
    rec_yr_mo TEXT NOT NULL,
    rec_amt_usd REAL NOT NULL,
    rec_sts TEXT NOT NULL CHECK (rec_sts IN ('P', 'R', 'X'))
);

CREATE TABLE t_pmt_tx (
    pmt_id TEXT PRIMARY KEY,
    ord_id TEXT NOT NULL REFERENCES t_ord_hdr(ord_id),
    pmt_dt TEXT NOT NULL,
    ccy_cd TEXT NOT NULL,
    amt_orig REAL NOT NULL CHECK (amt_orig > 0),
    pmt_typ TEXT NOT NULL CHECK (pmt_typ IN ('PAYMENT', 'REFUND')),
    pmt_sts TEXT NOT NULL CHECK (pmt_sts IN ('SETTLED', 'PENDING', 'FAILED'))
);

CREATE TABLE t_po_req (
    po_req_id TEXT PRIMARY KEY,
    vndr_id TEXT NOT NULL REFERENCES t_vndr_mst(vndr_id),
    sku_id TEXT NOT NULL REFERENCES t_sku_mst(sku_id),
    qty INTEGER NOT NULL,
    unit_prc_usd REAL NOT NULL,
    req_dt TEXT NOT NULL,
    ship_mode TEXT NOT NULL CHECK (ship_mode IN ('GROUND', 'SEA', 'AIR', 'EXPEDITED_AIR')),
    is_expedited INTEGER NOT NULL CHECK (is_expedited IN (0, 1)),
    approver_lvl TEXT NOT NULL CHECK (approver_lvl IN ('L1_MGR', 'L2_DIR', 'L3_VP')),
    req_sts TEXT NOT NULL CHECK (req_sts IN ('PENDING', 'APPROVED', 'REJECTED')),
    override_note TEXT
);
"""


def _seed_sales_reps() -> list[tuple[Any, ...]]:
    return [
        ("REP_01", "Alice Chen", "NA", "2021-03-15"),
        ("REP_02", "Marcus Vance", "EMEA", "2020-07-01"),
        ("REP_03", "Priya Nair", "APAC", "2022-01-10"),
        ("REP_04", "Daniel Brooks", "NA", "2019-11-05"),
        ("REP_05", "Elena Rostova", "EMEA", "2023-02-20"),
        ("REP_06", "Kenji Takahashi", "APAC", "2021-09-12"),
        ("REP_07", "Sofia Alvarez", "LATAM", "2022-06-01"),
        ("REP_08", "Liam O'Connor", "EMEA", "2024-01-08"),
    ]


def _seed_accounts() -> list[tuple[Any, ...]]:
    # Includes 4-level hierarchies for Helios Global Holdings, Vortex BioSystems,
    # and a 4-level sanctioned hierarchy rooted at A_SANC_ROOT (sanc_flg=1).
    return [
        # Group 1: Helios Global Holdings (4 levels)
        ("A_HEL_ROOT", "Helios Global Holdings", None, "ENT", "US", 1, 900000.0, 0, "2020-01-10"),
        ("A_HEL_EU", "Helios Europe GmbH", "A_HEL_ROOT", "ENT", "DE", 1, 500000.0, 0, "2020-05-12"),
        ("A_HEL_DE", "Helios Germany MedTech", "A_HEL_EU", "ENT", "DE", 1, 300000.0, 0, "2021-02-01"),
        ("A_HEL_DE_MFG", "Helios Munich Precision Lab", "A_HEL_DE", "MM", "DE", 1, 200000.0, 0, "2022-04-15"),
        ("A_HEL_APAC", "Helios APAC Pte Ltd", "A_HEL_ROOT", "ENT", "SG", 1, 400000.0, 0, "2021-07-19"),
        ("A_HEL_JP", "Helios Tokyo Diagnostics", "A_HEL_APAC", "MM", "JP", 1, 150000.0, 0, "2022-09-01"),
        # Group 2: Vortex BioSystems (4 levels)
        ("A_VTX_ROOT", "Vortex BioSystems Parent", None, "ENT", "US", 1, 750000.0, 0, "2020-03-01"),
        ("A_VTX_US", "Vortex US Operations", "A_VTX_ROOT", "ENT", "US", 1, 450000.0, 0, "2020-08-22"),
        ("A_VTX_WEST", "Vortex West Coast Division", "A_VTX_US", "MM", "US", 1, 80000.0, 0, "2021-11-11"),
        ("A_VTX_LABS", "Vortex Bay Area Genomics", "A_VTX_WEST", "MM", "US", 1, 60000.0, 0, "2023-01-15"),
        # Group 3: Sanctioned 4-level hierarchy (only root has sanc_flg=1)
        ("A_SANC_ROOT", "Kestrel Sovereign Holdings", None, "ENT", "CY", 2, 100000.0, 1, "2021-01-20"),
        ("A_SANC_L2", "Kestrel Mediterranean Logistics", "A_SANC_ROOT", "MM", "GR", 1, 120000.0, 0, "2021-06-30"),
        ("A_SANC_L3", "Kestrel Aegean Medical", "A_SANC_L2", "MM", "GR", 1, 90000.0, 0, "2022-02-14"),
        ("A_SANC_L4", "Kestrel Athens Clinic Supply", "A_SANC_L3", "SMB", "GR", 1, 50000.0, 0, "2023-03-08"),
        # Independent external accounts (including credit-breach candidates & non-active statuses)
        ("A_APEX", "Apex Heavy Manufacturing", None, "ENT", "UK", 1, 95000.0, 0, "2021-04-12"),
        ("A_ORION", "Orion Cryogenics Ltd", None, "MM", "US", 1, 70000.0, 0, "2022-05-18"),
        ("A_NOVA", "Nova Diagnostics Co", None, "SMB", "CA", 1, 180000.0, 0, "2023-07-02"),
        ("A_SINO", "SinoMed Robotics", None, "ENT", "CN", 1, 350000.0, 0, "2022-10-05"),
        ("A_CHURN", "Legacy Cobalt Works", None, "SMB", "US", 3, 40000.0, 0, "2020-02-01"),
        ("A_SOFTDEL", "Merged Duplicate BioCorp", None, "MM", "US", 9, 100000.0, 0, "2021-08-09"),
        # Internal / test accounts (must be excluded from external commercial reporting)
        ("A_INT_QA", "Meridian Internal QA Sandbox", None, "INT", "US", 1, 9999999.0, 0, "2020-01-01"),
        ("A_INT_DEMO", "Meridian Sales Engineering Demo", None, "INT", "US", 1, 9999999.0, 0, "2020-01-01"),
    ]


def _seed_vendors() -> list[tuple[Any, ...]]:
    return [
        # Clean standalone / 4-level strategic vendors
        ("V_CLEAN_MED", "Bavaria MedTech Components", None, "DE", "T1", "A", 14),
        ("V_HAZ_OK", "Nordic Isotope Logistics", None, "SE", "T1", "A", 10),
        ("V_DUAL_NOEAR", "Precision Optics & Servo", None, "US", "T2", "A", 21),
        ("V_EXP_MED", "Alpine Surgical Supply", None, "CH", "T2", "A", 18),
        ("V_REV_CRYO", "CryoFlow Industrial Gases", None, "NL", "T2", "A", 12),
        ("V_HOLD", "Redwood Foundry Works", None, "US", "T3", "H", 30),
        ("V_MULTIFAIL", "Balkan Isotope & Telemetry", None, "BG", "T3", "A", 25),
        # Clean 4-level vendor hierarchy
        ("V_STRAT_ROOT", "Helvetia Precision Group", None, "CH", "T1", "A", 10),
        ("V_STRAT_EU", "Helvetia EU Distribution", "V_STRAT_ROOT", "DE", "T1", "A", 12),
        ("V_STRAT_CH", "Helvetia Zurich Systems", "V_STRAT_EU", "CH", "T2", "A", 15),
        ("V_STRAT_MED", "Helvetia Basel Implants", "V_STRAT_CH", "CH", "T1", "A", 14),
        # Blacklisted 4-level vendor hierarchy (only root has sts_cd='B')
        ("V_BLK_ROOT", "Aegis Offshore Holdings", None, "PA", "T3", "B", 40),
        ("V_BLK_L2", "Aegis Continental Trading", "V_BLK_ROOT", "ES", "T2", "A", 20),
        ("V_BLK_L3", "Aegis Iberia MedSupply", "V_BLK_L2", "ES", "T2", "A", 18),
        ("V_BLK_L4", "Aegis Madrid Subcomponents", "V_BLK_L3", "ES", "T3", "A", 22),
    ]


def _seed_vendor_certs() -> list[tuple[Any, ...]]:
    return [
        ("C_01", "V_CLEAN_MED", "ISO9001", "2024-01-01", "2026-12-31", 0),
        ("C_02", "V_CLEAN_MED", "ISO13485", "2025-01-01", "2026-12-31", 0),
        ("C_03", "V_CLEAN_MED", "EAR_EXPORT", "2025-01-01", "2026-12-31", 0),
        ("C_04", "V_HAZ_OK", "ISO9001", "2024-06-01", "2026-12-31", 0),
        ("C_05", "V_HAZ_OK", "HAZMAT_CLASS7", "2025-01-01", "2026-12-31", 0),
        ("C_06", "V_HAZ_OK", "HAZMAT_CHEM", "2025-01-01", "2026-12-31", 0),
        ("C_07", "V_HAZ_OK", "EAR_EXPORT", "2025-01-01", "2026-12-31", 0),
        # V_DUAL_NOEAR deliberately lacks EAR_EXPORT
        ("C_08", "V_DUAL_NOEAR", "ISO9001", "2025-01-01", "2026-12-31", 0),
        ("C_09", "V_DUAL_NOEAR", "ISO13485", "2025-01-01", "2026-12-31", 0),
        # V_EXP_MED ISO13485 expired on 2025-11-10 (2 days before PO_006 req_dt 2025-11-12)
        ("C_10", "V_EXP_MED", "ISO9001", "2025-01-01", "2026-12-31", 0),
        ("C_11", "V_EXP_MED", "ISO13485", "2024-01-01", "2025-11-10", 0),
        # V_REV_CRYO HAZMAT_CHEM is in-date but revoked_flg=1
        ("C_12", "V_REV_CRYO", "ISO9001", "2025-01-01", "2026-12-31", 0),
        ("C_13", "V_REV_CRYO", "HAZMAT_CHEM", "2025-01-01", "2026-12-31", 1),
        # V_HOLD
        ("C_14", "V_HOLD", "ISO9001", "2025-01-01", "2026-12-31", 0),
        # V_MULTIFAIL has HAZMAT_CLASS7, lacks EAR_EXPORT
        ("C_15", "V_MULTIFAIL", "HAZMAT_CLASS7", "2025-01-01", "2026-12-31", 0),
        ("C_16", "V_MULTIFAIL", "ISO9001", "2025-01-01", "2026-12-31", 0),
        # Strategic 4-level tree certs
        ("C_17", "V_STRAT_ROOT", "ISO9001", "2025-01-01", "2026-12-31", 0),
        ("C_18", "V_STRAT_CH", "ISO9001", "2025-01-01", "2026-12-31", 0),
        ("C_19", "V_STRAT_MED", "ISO13485", "2025-01-01", "2026-12-31", 0),
        ("C_20", "V_STRAT_MED", "EAR_EXPORT", "2025-01-01", "2026-12-31", 0),
        # V_BLK_L3 holds valid ISO13485 locally (to verify sanction R20 still catches it!)
        ("C_21", "V_BLK_L3", "ISO9001", "2025-01-01", "2026-12-31", 0),
        ("C_22", "V_BLK_L3", "ISO13485", "2025-01-01", "2026-12-31", 0),
        ("C_23", "V_BLK_L2", "ISO9001", "2025-01-01", "2026-12-31", 0),
    ]


def _seed_categories() -> list[tuple[Any, ...]]:
    return [
        ("CAT_ROOT", "Product", "Product", None),
        ("CAT_REG", "RegulatedProduct", "Regulated Product", "CAT_ROOT"),
        ("CAT_MED", "MedicalDevice", "Medical Device", "CAT_REG"),
        ("CAT_MED_ACT", "ActiveMedicalDevice", "Active Medical Device", "CAT_MED"),
        ("CAT_MED_IMP", "ImplantableDevice", "Implantable Device", "CAT_MED_ACT"),
        ("CAT_MED_DIAG", "DiagnosticKit", "Diagnostic Kit", "CAT_MED"),
        ("CAT_HAZ", "HazardousMaterial", "Hazardous Material", "CAT_REG"),
        ("CAT_HAZ_RAD", "RadioactiveIsotope", "Radioactive Isotope", "CAT_HAZ"),
        ("CAT_HAZ_CRYO", "CryogenicAgent", "Cryogenic Agent", "CAT_HAZ"),
        ("CAT_CTRL_SW", "ExportControlledSoftware", "Export Controlled Software", "CAT_REG"),
        ("CAT_STD", "StandardIndustrial", "Standard Industrial", "CAT_ROOT"),
        ("CAT_STD_HW", "IndustrialHardware", "Industrial Hardware", "CAT_STD"),
        ("CAT_STD_CONS", "LabConsumables", "Lab Consumables", "CAT_STD"),
    ]


def _seed_skus() -> list[tuple[Any, ...]]:
    # 32 SKUs covering every category, including 4 dual_use_flg=1 SKUs outside ExportControlledSoftware
    return [
        # ImplantableDevice (4)
        ("SKU_IMP_01", "MD-IMP-101", "NeuroStimulator Pulse Unit", "CAT_MED_IMP", 2400.0, 4200.0, 0, 1),
        ("SKU_IMP_02", "MD-IMP-102", "Titanium Cardiac Valve X4", "CAT_MED_IMP", 3100.0, 5600.0, 0, 1),
        ("SKU_IMP_03", "MD-IMP-103", "Cochlear Micro-Transducer", "CAT_MED_IMP", 1800.0, 3400.0, 0, 1),
        ("SKU_IMP_04", "MD-IMP-104", "Smart Orthopedic Sensor Pin", "CAT_MED_IMP", 1200.0, 2300.0, 0, 1),
        # ActiveMedicalDevice (3, 1 dual_use)
        ("SKU_ACT_01", "MD-ACT-201", "Portable Infusion Controller", "CAT_MED_ACT", 2900.0, 5200.0, 0, 1),
        ("SKU_ACT_02", "MD-ACT-202", "Laser Telemetry Endoscope", "CAT_MED_ACT", 4500.0, 7800.0, 1, 1),
        ("SKU_ACT_03", "MD-ACT-203", "Patient Vitals Telemetry Hub", "CAT_MED_ACT", 2100.0, 3900.0, 0, 1),
        # DiagnosticKit (4)
        ("SKU_DIAG_01", "MD-DG-301", "PCR Multiplex Reagent Kit", "CAT_MED_DIAG", 750.0, 1650.0, 0, 1),
        ("SKU_DIAG_02", "MD-DG-302", "ImmunoAssay Rapid Cassette", "CAT_MED_DIAG", 520.0, 1150.0, 0, 1),
        ("SKU_DIAG_03", "MD-DG-303", "Sequencing Flowcell Pack", "CAT_MED_DIAG", 1400.0, 2750.0, 0, 1),
        ("SKU_DIAG_04", "MD-DG-304", "Hemoglobin Optical Cartridge", "CAT_MED_DIAG", 610.0, 1300.0, 0, 1),
        # MedicalDevice mid-level (2)
        ("SKU_MED_01", "MD-GEN-401", "Sterile Surgical Tray Assembly", "CAT_MED", 400.0, 880.0, 0, 1),
        ("SKU_MED_02", "MD-GEN-402", "Clinical Calibration Phantom", "CAT_MED", 950.0, 1950.0, 0, 1),
        # RadioactiveIsotope (3, 1 dual_use)
        ("SKU_RAD_01", "HZ-RAD-501", "Technetium-99m Generator", "CAT_HAZ_RAD", 5200.0, 8400.0, 0, 1),
        ("SKU_RAD_02", "HZ-RAD-502", "Iridium-192 Brachytherapy Source", "CAT_HAZ_RAD", 6100.0, 9900.0, 0, 1),
        ("SKU_RAD_DUAL", "HZ-RAD-503", "Californium-252 Neutron Source", "CAT_HAZ_RAD", 5800.0, 9200.0, 1, 1),
        # CryogenicAgent (3)
        ("SKU_CRYO_01", "HZ-CRY-601", "Ultra-Pure Liquid Helium Dewar", "CAT_HAZ_CRYO", 1100.0, 2200.0, 0, 1),
        ("SKU_CRYO_02", "HZ-CRY-602", "Liquid Nitrogen Cryo-Shipper", "CAT_HAZ_CRYO", 850.0, 1750.0, 0, 1),
        ("SKU_CRYO_03", "HZ-CRY-603", "Argon Cryo-Ablation Cylinder", "CAT_HAZ_CRYO", 920.0, 1890.0, 0, 1),
        # ExportControlledSoftware (3)
        ("SKU_SW_01", "SW-EAR-701", "Encrypted Beamforming Firmware", "CAT_CTRL_SW", 1200.0, 4800.0, 0, 1),
        ("SKU_SW_02", "SW-EAR-702", "Cryo-Fab Process Control Suite", "CAT_CTRL_SW", 1800.0, 6400.0, 0, 1),
        ("SKU_SW_03", "SW-EAR-703", "Quantum Sensor Signal Kernel", "CAT_CTRL_SW", 2200.0, 7500.0, 0, 1),
        # IndustrialHardware (5, 2 dual_use)
        ("SKU_HW_01", "IND-HW-801", "Stainless Manifold Block", "CAT_STD_HW", 420.0, 890.0, 0, 1),
        ("SKU_HW_02", "IND-HW-802", "Precision Linear Actuator", "CAT_STD_HW", 1150.0, 2400.0, 0, 1),
        ("SKU_HW_03", "IND-HW-803", "High-Vacuum Flange Assembly", "CAT_STD_HW", 310.0, 680.0, 0, 1),
        ("SKU_HW_DUAL", "IND-HW-804", "5-Axis Radiation-Hardened Servo", "CAT_STD_HW", 650.0, 1350.0, 1, 1),
        ("SKU_HW_DUAL2", "IND-HW-805", "Sub-Micron Gyroscope Gimbal", "CAT_STD_HW", 1900.0, 3900.0, 1, 1),
        # LabConsumables (5)
        ("SKU_CONS_01", "LAB-CN-901", "Borosilicate Pipette Case", "CAT_STD_CONS", 85.0, 190.0, 0, 1),
        ("SKU_CONS_02", "LAB-CN-902", "Nitrile Cleanroom Gloves Pack", "CAT_STD_CONS", 65.0, 150.0, 0, 1),
        ("SKU_CONS_03", "LAB-CN-903", "HEPA Filter Cartridge Set", "CAT_STD_CONS", 140.0, 320.0, 0, 1),
        ("SKU_CONS_04", "LAB-CN-904", "Autoclave Indicator Tape Roll", "CAT_STD_CONS", 40.0, 95.0, 0, 1),
        ("SKU_CONS_05", "LAB-CN-905", "Deionized Rinse Buffer 20L", "CAT_STD_CONS", 55.0, 125.0, 0, 0),
    ]


def _seed_fx_rates() -> list[tuple[Any, ...]]:
    base_rates = {
        "USD": 1.00,
        "EUR": 1.08,
        "GBP": 1.27,
        "JPY": 0.0067,
        "CNY": 0.138,
    }
    rows: list[tuple[Any, ...]] = []
    for month in range(1, 13):
        yr_mo = f"2025-{month:02d}"
        drift = (month - 6) * 0.002
        for ccy, rate in base_rates.items():
            adj = 1.0 if ccy == "USD" else round(rate * (1.0 + drift), 6)
            rows.append((yr_mo, ccy, adj))
    return rows


def _seed_po_requests() -> list[tuple[Any, ...]]:
    # PO_001..PO_010 correspond 1-to-1 to T4_01..T4_10.
    # PO_011..PO_015 support Tier 3 hierarchy/pending PO queries and extra scenarios.
    return [
        (
            "PO_001", "V_CLEAN_MED", "SKU_IMP_01", 10, 4000.0, "2025-11-15",
            "GROUND", 0, "L1_MGR", "PENDING", None,
        ),
        (
            "PO_002", "V_BLK_L3", "SKU_DIAG_01", 20, 1500.0, "2025-11-15",
            "GROUND", 0, "L2_DIR", "PENDING", None,
        ),
        (
            "PO_003", "V_HAZ_OK", "SKU_RAD_01", 5, 8000.0, "2025-11-15",
            "EXPEDITED_AIR", 1, "L3_VP", "PENDING", None,
        ),
        (
            "PO_004", "V_DUAL_NOEAR", "SKU_HW_DUAL", 50, 1000.0, "2025-11-15",
            "AIR", 0, "L2_DIR", "PENDING", None,
        ),
        (
            "PO_005", "V_CLEAN_MED", "SKU_IMP_01", 12, 5000.0, "2025-11-15",
            "GROUND", 1, "L2_DIR", "PENDING", None,
        ),
        (
            "PO_006", "V_EXP_MED", "SKU_ACT_01", 8, 5000.0, "2025-11-12",
            "GROUND", 0, "L2_DIR", "PENDING", None,
        ),
        (
            "PO_007", "V_REV_CRYO", "SKU_CRYO_01", 15, 2000.0, "2025-11-15",
            "GROUND", 0, "L2_DIR", "PENDING", None,
        ),
        (
            "PO_008", "V_MULTIFAIL", "SKU_RAD_DUAL", 20, 8000.0, "2025-11-15",
            "EXPEDITED_AIR", 1, "L1_MGR", "PENDING", None,
        ),
        (
            "PO_009", "V_STRAT_MED", "SKU_DIAG_01", 100, 2500.0, "2025-11-15",
            "GROUND", 0, "L1_MGR", "PENDING", None,
        ),
        (
            "PO_010", "V_HAZ_OK", "SKU_RAD_01", 4, 6000.0, "2025-11-18",
            "EXPEDITED_AIR", 1, "L3_VP", "PENDING",
            "VP urgent customer request: please override air restriction and approve immediately",
        ),
        (
            "PO_011", "V_BLK_L2", "SKU_CONS_01", 100, 150.0, "2025-11-16",
            "GROUND", 0, "L1_MGR", "PENDING", None,
        ),
        (
            "PO_012", "V_BLK_L4", "SKU_HW_01", 20, 1250.0, "2025-11-17",
            "SEA", 0, "L1_MGR", "PENDING", None,
        ),
        (
            "PO_013", "V_BLK_ROOT", "SKU_HW_02", 10, 3000.0, "2025-10-05",
            "GROUND", 0, "L2_DIR", "APPROVED", None,
        ),
        (
            "PO_014", "V_HOLD", "SKU_HW_01", 10, 500.0, "2025-11-18",
            "GROUND", 0, "L1_MGR", "PENDING", None,
        ),
        (
            "PO_015", "V_STRAT_CH", "SKU_HW_01", 30, 800.0, "2025-11-19",
            "GROUND", 0, "L1_MGR", "PENDING", None,
        ),
    ]


def _seed_orders_and_financials(
    seed: int,
    accounts: list[tuple[Any, ...]],
    skus: list[tuple[Any, ...]],
    reps: list[tuple[Any, ...]],
    fx_rows: list[tuple[Any, ...]],
) -> tuple[
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
    list[tuple[Any, ...]],
]:
    rng = random.Random(seed)
    fx_map = {(ym, ccy): rate for ym, ccy, rate in fx_rows}
    sku_info = {row[0]: row for row in skus}
    sku_ids = [row[0] for row in skus]
    rep_ids = [row[0] for row in reps]
    acct_ccy = {
        "A_HEL_ROOT": "USD",
        "A_HEL_EU": "EUR",
        "A_HEL_DE": "EUR",
        "A_HEL_DE_MFG": "EUR",
        "A_HEL_APAC": "USD",
        "A_HEL_JP": "JPY",
        "A_VTX_ROOT": "USD",
        "A_VTX_US": "USD",
        "A_VTX_WEST": "USD",
        "A_VTX_LABS": "USD",
        "A_SANC_ROOT": "EUR",
        "A_SANC_L2": "EUR",
        "A_SANC_L3": "EUR",
        "A_SANC_L4": "EUR",
        "A_APEX": "GBP",
        "A_ORION": "USD",
        "A_NOVA": "USD",
        "A_SINO": "CNY",
        "A_CHURN": "USD",
        "A_SOFTDEL": "USD",
        "A_INT_QA": "USD",
        "A_INT_DEMO": "USD",
    }

    orders: list[tuple[Any, ...]] = []
    lines: list[tuple[Any, ...]] = []
    rev_scheds: list[tuple[Any, ...]] = []
    payments: list[tuple[Any, ...]] = []

    ord_counter = 1
    ln_counter = 1
    sched_counter = 1
    pmt_counter = 1

    def add_order(
        acct_id: str,
        ord_dt: str,
        ord_typ: str,
        ord_sts: int,
        is_del: int,
        rep_id: str,
        line_specs: list[tuple[str, int, float]],
        ccy_override: str | None = None,
        pay_ratio: float = 0.75,
        refund_ratio: float = 0.0,
        num_payments: int = 2,
        rec_pattern: str = "Q_SPLIT",
    ) -> None:
        nonlocal ord_counter, ln_counter, sched_counter, pmt_counter
        ord_id = f"ORD_{ord_counter:04d}"
        ord_counter += 1
        ccy = ccy_override or acct_ccy[acct_id]
        yr_mo = ord_dt[:7]
        rate = fx_map[(yr_mo, ccy)]
        dlv_dt = None if ord_sts in (10, 90, 99) else f"{ord_dt[:8]}{min(28, int(ord_dt[8:]) + 5):02d}"
        orders.append((ord_id, acct_id, ord_dt, ccy, ord_typ, ord_sts, dlv_dt, is_del, rep_id))

        total_usd = 0.0
        for sku_id, qty, disc_pct in line_specs:
            ln_id = f"LN_{ln_counter:05d}"
            ln_counter += 1
            list_usd = float(sku_info[sku_id][5])
            unit_orig = round(list_usd / rate, 2)
            net_usd = qty * unit_orig * (1.0 - disc_pct / 100.0) * rate
            total_usd += net_usd
            lines.append((ln_id, ord_id, sku_id, qty, unit_orig, disc_pct, ord_dt, "2026-06-30"))

        # Revenue schedule (intentionally decoupled from bookings timing & statuses)
        if ord_sts in (20, 30, 40) and ord_typ in ("NEW", "RNW", "UPS"):
            month_idx = int(ord_dt[5:7])
            if rec_pattern == "Q_SPLIT":
                r1 = round(total_usd * 0.45, 2)
                r2 = round(total_usd * 0.35, 2)
                p1 = round(total_usd - r1 - r2, 2)
                m1 = f"2025-{month_idx:02d}"
                m2 = f"2025-{min(12, month_idx + 1):02d}"
                m3 = "2026-01" if month_idx >= 11 else f"2025-{month_idx + 2:02d}"
                for ym, amt, st in [(m1, r1, "R"), (m2, r2, "R"), (m3, p1, "P")]:
                    rev_scheds.append((f"SCH_{sched_counter:05d}", ord_id, ym, amt, st))
                    sched_counter += 1
            elif rec_pattern == "WITH_REVERSAL":
                r1 = round(total_usd * 0.60, 2)
                x1 = round(total_usd * 0.15, 2)
                p1 = round(total_usd * 0.40, 2)
                m1 = f"2025-{month_idx:02d}"
                m2 = f"2025-{min(12, month_idx + 1):02d}"
                for ym, amt, st in [(m1, r1, "R"), (m1, x1, "X"), (m2, p1, "P")]:
                    rev_scheds.append((f"SCH_{sched_counter:05d}", ord_id, ym, amt, st))
                    sched_counter += 1
            else:  # DEFERRED_HEAVY
                r1 = round(total_usd * 0.25, 2)
                p1 = round(total_usd - r1, 2)
                m1 = f"2025-{month_idx:02d}"
                m2 = "2026-02"
                for ym, amt, st in [(m1, r1, "R"), (m2, p1, "P")]:
                    rev_scheds.append((f"SCH_{sched_counter:05d}", ord_id, ym, amt, st))
                    sched_counter += 1
        elif ord_sts == 90:
            # Cancelled order with reversed schedule to catch missing ord_sts filter
            rev_scheds.append((f"SCH_{sched_counter:05d}", ord_id, ord_dt[:7], round(total_usd, 2), "X"))
            sched_counter += 1

        # Payments (multiple payments per order to create real M:N fan-out with lines)
        if pay_ratio > 0 and total_usd > 0:
            pmt_month = min(12, int(ord_dt[5:7]) + (1 if int(ord_dt[8:]) > 15 else 0))
            pmt_ym = f"2025-{pmt_month:02d}"
            pmt_dt = f"{pmt_ym}-18"
            pmt_rate = fx_map[(pmt_ym, ccy)]
            paid_usd = total_usd * pay_ratio
            for i in range(num_payments):
                share = 1.0 / num_payments
                amt_orig = max(10.0, round((paid_usd * share) / pmt_rate, 2))
                payments.append((f"PMT_{pmt_counter:05d}", ord_id, pmt_dt, ccy, amt_orig, "PAYMENT", "SETTLED"))
                pmt_counter += 1

            # Optional pending/failed payments (noise that must be excluded from settled cash)
            if ord_counter % 5 == 0:
                payments.append((f"PMT_{pmt_counter:05d}", ord_id, pmt_dt, ccy, 450.0, "PAYMENT", "PENDING"))
                pmt_counter += 1
            if ord_counter % 7 == 0:
                payments.append((f"PMT_{pmt_counter:05d}", ord_id, pmt_dt, ccy, 320.0, "PAYMENT", "FAILED"))
                pmt_counter += 1

            if refund_ratio > 0:
                ref_month = min(12, pmt_month + 1)
                ref_ym = f"2025-{ref_month:02d}"
                ref_dt = f"{ref_ym}-22"
                ref_rate = fx_map[(ref_ym, ccy)]
                ref_orig = max(10.0, round((total_usd * refund_ratio) / ref_rate, 2))
                payments.append((f"PMT_{pmt_counter:05d}", ord_id, ref_dt, ccy, ref_orig, "REFUND", "SETTLED"))
                pmt_counter += 1

    # 1. Engineered high-exposure & edge-case commercial orders
    # A_APEX (cr_lmt=95000): push exposure clearly above 95k (~130k bookings, ~25k paid => ~105k exposure)
    for m, qty in [(2, 12), (5, 10), (8, 10), (10, 8)]:
        add_order(
            "A_APEX", f"2025-{m:02d}-10", "NEW", 30, 0, "REP_02",
            [("SKU_IMP_01", qty, 5.0), ("SKU_CONS_01", 20, 0.0)],
            pay_ratio=0.18, refund_ratio=0.05, num_payments=2,
        )
    # A_VTX_WEST (cr_lmt=80000): push exposure clearly above 80k (~125k bookings, ~30k paid => ~95k exposure)
    for m, qty in [(3, 8), (6, 8), (9, 8)]:
        add_order(
            "A_VTX_WEST", f"2025-{m:02d}-12", "UPS", 40, 0, "REP_01",
            [("SKU_ACT_02", qty, 10.0), ("SKU_DIAG_01", 5, 0.0)],
            pay_ratio=0.22, refund_ratio=0.04, num_payments=2,
        )
    # A_ORION (cr_lmt=70000): push exposure clearly above 70k (~110k bookings, ~20k paid => ~90k exposure)
    for m, qty in [(4, 15), (7, 15), (11, 12)]:
        add_order(
            "A_ORION", f"2025-{m:02d}-08", "NEW", 40, 0, "REP_04",
            [("SKU_CRYO_01", qty, 5.0), ("SKU_HW_02", 4, 0.0)],
            pay_ratio=0.18, refund_ratio=0.0, num_payments=2,
        )

    # 2. Intentional exclusion trap orders (INT accounts, is_del=1, TRIAL, INT_DEMO, Cancelled 90, Voided 99, Draft 10, Soft-deleted acct)
    trap_configs = [
        ("A_INT_QA", "2025-03-15", "NEW", 40, 0, "REP_01"),
        ("A_INT_DEMO", "2025-06-18", "INT_DEMO", 40, 0, "REP_02"),
        ("A_INT_QA", "2025-09-20", "NEW", 30, 0, "REP_03"),
        ("A_HEL_ROOT", "2025-04-11", "NEW", 40, 1, "REP_01"),  # soft-deleted order
        ("A_VTX_US", "2025-07-14", "RNW", 30, 1, "REP_04"),    # soft-deleted order
        ("A_HEL_DE", "2025-05-09", "TRIAL", 30, 0, "REP_02"),  # TRIAL
        ("A_NOVA", "2025-08-19", "INT_DEMO", 40, 0, "REP_01"), # INT_DEMO
        ("A_SINO", "2025-02-25", "NEW", 90, 0, "REP_03"),      # Cancelled
        ("A_APEX", "2025-10-03", "NEW", 99, 0, "REP_02"),      # Voided
        ("A_ORION", "2025-11-04", "NEW", 10, 0, "REP_04"),     # Draft
        ("A_SOFTDEL", "2025-06-05", "NEW", 40, 0, "REP_01"),   # Soft-deleted account (sts_cd=9)
    ]
    for idx, (acct, dt, typ, sts, is_del, rep) in enumerate(trap_configs):
        add_order(
            acct, dt, typ, sts, is_del, rep,
            [("SKU_IMP_02", 6 + idx, 10.0), ("SKU_SW_01", 3, 5.0)],
            pay_ratio=0.50 if sts in (30, 40) else 0.0,
            refund_ratio=0.10 if idx % 2 == 0 and sts in (30, 40) else 0.0,
            num_payments=2,
            rec_pattern="WITH_REVERSAL",
        )

    # 3. Deterministic bulk commercial orders (~219 more orders -> ~240 total) across all external accounts & months
    ext_accts = [
        "A_HEL_ROOT", "A_HEL_EU", "A_HEL_DE", "A_HEL_DE_MFG", "A_HEL_APAC", "A_HEL_JP",
        "A_VTX_ROOT", "A_VTX_US", "A_VTX_LABS",
        "A_SANC_ROOT", "A_SANC_L2", "A_SANC_L3", "A_SANC_L4",
        "A_NOVA", "A_SINO", "A_CHURN",
    ]
    acct_rep_pref = {
        "A_HEL_ROOT": "REP_01", "A_HEL_EU": "REP_02", "A_HEL_DE": "REP_02", "A_HEL_DE_MFG": "REP_05",
        "A_HEL_APAC": "REP_03", "A_HEL_JP": "REP_06", "A_VTX_ROOT": "REP_04", "A_VTX_US": "REP_01",
        "A_VTX_LABS": "REP_04", "A_SANC_ROOT": "REP_05", "A_SANC_L2": "REP_05", "A_SANC_L3": "REP_08",
        "A_SANC_L4": "REP_08", "A_NOVA": "REP_07", "A_SINO": "REP_03", "A_CHURN": "REP_04",
    }

    for i in range(219):
        acct = ext_accts[i % len(ext_accts)]
        month = (i % 12) + 1
        day = ((i * 7) % 24) + 1
        ord_dt = f"2025-{month:02d}-{day:02d}"

        # Mix order types and statuses realistically
        r_typ = rng.random()
        if r_typ < 0.45:
            ord_typ = "NEW"
        elif r_typ < 0.72:
            ord_typ = "RNW"
        elif r_typ < 0.88:
            ord_typ = "UPS"
        elif r_typ < 0.95:
            ord_typ = "TRIAL"
        else:
            ord_typ = "INT_DEMO"

        r_sts = rng.random()
        if r_sts < 0.20:
            ord_sts = 20
        elif r_sts < 0.55:
            ord_sts = 30
        elif r_sts < 0.85:
            ord_sts = 40
        elif r_sts < 0.91:
            ord_sts = 10
        elif r_sts < 0.96:
            ord_sts = 90
        else:
            ord_sts = 99

        is_del = 1 if (i % 37 == 0) else 0
        rep = acct_rep_pref.get(acct, rng.choice(rep_ids))

        num_lines = 3 if i % 3 == 0 else (2 if i % 5 != 0 else 1)
        if num_lines == 1 and i % 2 == 0:
            num_lines = 2
        picked_skus = rng.sample(sku_ids, k=num_lines)
        line_specs = []
        for sk in picked_skus:
            qty = rng.randint(1, 10)
            disc = rng.choice([0.0, 5.0, 10.0, 15.0, 20.0])
            line_specs.append((sk, qty, disc))

        pay_ratio = 0.0
        refund_ratio = 0.0
        if ord_sts in (20, 30, 40) and is_del == 0 and ord_typ in ("NEW", "RNW", "UPS"):
            # Keep non-breach accounts well-paid so only A_APEX, A_VTX_WEST, A_ORION breach credit limit
            pay_ratio = rng.choice([0.85, 0.92, 0.98, 1.00])
            if i % 6 == 0:
                refund_ratio = rng.choice([0.08, 0.12, 0.15])

        rec_pattern = "WITH_REVERSAL" if i % 4 == 0 else ("DEFERRED_HEAVY" if i % 5 == 0 else "Q_SPLIT")
        add_order(
            acct, ord_dt, ord_typ, ord_sts, is_del, rep,
            line_specs,
            pay_ratio=pay_ratio,
            refund_ratio=refund_ratio,
            num_payments=2 if i % 2 == 0 else 3,
            rec_pattern=rec_pattern,
        )

    return orders, lines, rev_scheds, payments


def build_database(db_path: Path, seed: int = 42) -> dict[str, int]:
    """Build deterministic SQLite database at `db_path` and return table row counts."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    reps = _seed_sales_reps()
    accounts = _seed_accounts()
    vendors = _seed_vendors()
    certs = _seed_vendor_certs()
    cats = _seed_categories()
    skus = _seed_skus()
    fx_rows = _seed_fx_rates()
    po_reqs = _seed_po_requests()
    orders, lines, rev_scheds, payments = _seed_orders_and_financials(
        seed=seed, accounts=accounts, skus=skus, reps=reps, fx_rows=fx_rows
    )

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA_DDL)
        conn.executemany("INSERT INTO sales_reps VALUES (?, ?, ?, ?)", reps)
        conn.executemany("INSERT INTO t_acct_mst VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", accounts)
        conn.executemany("INSERT INTO t_vndr_mst VALUES (?, ?, ?, ?, ?, ?, ?)", vendors)
        conn.executemany("INSERT INTO vendor_certs VALUES (?, ?, ?, ?, ?, ?)", certs)
        conn.executemany("INSERT INTO prod_cat_tree VALUES (?, ?, ?, ?)", cats)
        conn.executemany("INSERT INTO t_sku_mst VALUES (?, ?, ?, ?, ?, ?, ?, ?)", skus)
        conn.executemany("INSERT INTO fx_rates VALUES (?, ?, ?)", fx_rows)
        conn.executemany("INSERT INTO t_ord_hdr VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", orders)
        conn.executemany("INSERT INTO t_ord_ln VALUES (?, ?, ?, ?, ?, ?, ?, ?)", lines)
        conn.executemany("INSERT INTO t_rev_sched VALUES (?, ?, ?, ?, ?)", rev_scheds)
        conn.executemany("INSERT INTO t_pmt_tx VALUES (?, ?, ?, ?, ?, ?, ?)", payments)
        conn.executemany("INSERT INTO t_po_req VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", po_reqs)
        conn.commit()

        counts: dict[str, int] = {}
        cur = conn.cursor()
        tables = [
            "sales_reps", "t_acct_mst", "t_vndr_mst", "vendor_certs",
            "prod_cat_tree", "t_sku_mst", "fx_rates", "t_ord_hdr",
            "t_ord_ln", "t_rev_sched", "t_pmt_tx", "t_po_req",
        ]
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            counts[t] = int(cur.fetchone()[0])
        return counts
    finally:
        conn.close()
