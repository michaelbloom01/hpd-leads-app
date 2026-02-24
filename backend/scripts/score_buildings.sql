-- ============================================================
-- Building Churn Scoring - Pure SQL Implementation
-- ============================================================
-- Weights (Default config, total=100):
--   ownership_change:20, complaint_spike:17, violation_trend:12
--   energy_grade_drop:8 (NULL-able), dob_permits:8
--   hpd_litigation:9, emergency_repairs:5
--   building_size:5, eviction_activity:9, facade_status:7 (NULL-able)
--
-- energy_grade_drop (8) and facade_status (7) are always NULL
-- (tables not loaded), so null_weight_pool=15, non_null_total=85
--
-- Effective weights = base + (15 * base / 85):
--   ownership_change:  20 + 3.529 = 23.529
--   complaint_spike:   17 + 3.000 = 20.000
--   violation_trend:   12 + 2.118 = 14.118
--   dob_permits:        8 + 1.412 =  9.412 (raw always 0, no data)
--   hpd_litigation:     9 + 1.588 = 10.588
--   emergency_repairs:  5 + 0.882 =  5.882 (raw always 0, no data)
--   building_size:      5 + 0.882 =  5.882
--   eviction_activity:  9 + 1.588 = 10.588 (raw always 0, no data)
-- ============================================================

BEGIN;

-- ── 1. Signal views ──────────────────────────────────────────

CREATE OR REPLACE VIEW signal_complaints AS
SELECT bbl,
       COUNT(*) FILTER (WHERE received_date > now() - interval '6 months')  AS complaints_6mo,
       COUNT(*) FILTER (WHERE received_date > now() - interval '12 months') AS complaints_12mo
FROM hpd_complaints GROUP BY bbl;

CREATE OR REPLACE VIEW signal_violations AS
SELECT bbl,
       COUNT(*) FILTER (WHERE inspection_date > now() - interval '6 months')  AS violations_6mo,
       COUNT(*) FILTER (WHERE inspection_date > now() - interval '12 months') AS violations_12mo
FROM hpd_violations GROUP BY bbl;

CREATE OR REPLACE VIEW signal_litigation AS
SELECT bbl,
       BOOL_OR(case_status IN ('OPEN', 'PENDING'))                           AS active_litigation,
       COUNT(*) FILTER (WHERE case_close_date > now() - interval '24 months') AS recent_closed,
       BOOL_OR(finding ILIKE '%harassment%')                                  AS harassment_finding
FROM hpd_litigation GROUP BY bbl;

CREATE OR REPLACE VIEW signal_aep AS
SELECT bbl, true AS is_aep FROM aep_designations WHERE is_active = true;

-- ── 2. Drop & recreate materialized view ────────────────────

DROP MATERIALIZED VIEW IF EXISTS building_signal_summary;

CREATE MATERIALIZED VIEW building_signal_summary AS
SELECT
    b.bbl,
    b.unit_count,
    COALESCE(c.complaints_6mo,  0)                AS complaints_6mo,
    COALESCE(c.complaints_12mo, 0)                AS complaints_12mo,
    false                                          AS sale_12mo,
    false                                          AS sale_24mo,
    false                                          AS refi_12mo,
    COALESCE(v.violations_6mo,  0)                AS violations_6mo,
    COALESCE(v.violations_12mo, 0)                AS violations_12mo,
    0                                              AS permits_12mo,
    0.0                                            AS permit_cost_12mo,
    COALESCE(l.active_litigation, false)           AS active_litigation,
    COALESCE(l.recent_closed, 0)                  AS recent_closed,
    COALESCE(l.harassment_finding, false)          AS harassment_finding,
    0                                              AS erp_12mo,
    0.0                                            AS erp_amount_12mo,
    NULL::text                                     AS current_grade,
    false                                          AS grade_dropped,
    0                                              AS eviction_filings_12mo,
    NULL::int                                      AS facade_score,
    COALESCE(aep.is_aep, false)                   AS is_aep
FROM buildings b
LEFT JOIN signal_complaints  c   USING(bbl)
LEFT JOIN signal_violations  v   USING(bbl)
LEFT JOIN signal_litigation  l   USING(bbl)
LEFT JOIN signal_aep         aep USING(bbl);

-- ── 3. Score computation ─────────────────────────────────────

WITH raw_signals AS (
    SELECT
        bss.bbl,
        bss.unit_count,
        -- ownership_change: no ACRIS data loaded → always 0
        0.0 AS ownership_change_raw,
        -- complaint_spike
        CASE
            WHEN bss.complaints_12mo - bss.complaints_6mo = 0
                 THEN CASE WHEN bss.complaints_6mo > 0 THEN 50.0 ELSE 0.0 END
            WHEN bss.complaints_6mo::float / GREATEST(bss.complaints_12mo - bss.complaints_6mo, 1) >= 2.0
                 THEN 100.0
            WHEN bss.complaints_6mo::float / GREATEST(bss.complaints_12mo - bss.complaints_6mo, 1) >= 1.5
                 THEN 60.0
            WHEN bss.complaints_6mo::float / GREATEST(bss.complaints_12mo - bss.complaints_6mo, 1) >= 1.0
                 THEN 20.0
            ELSE 0.0
        END AS complaint_spike_raw,
        -- violation_trend
        CASE
            WHEN bss.violations_12mo - bss.violations_6mo = 0
                 THEN LEAST(100.0, bss.violations_6mo::float / GREATEST(COALESCE(bss.unit_count, 1), 1) * 100)
            ELSE LEAST(100.0, bss.violations_6mo::float / GREATEST(bss.violations_12mo - bss.violations_6mo, 1) * 50)
        END AS violation_trend_raw,
        -- hpd_litigation
        CASE
            WHEN bss.active_litigation OR bss.is_aep OR bss.harassment_finding THEN 100.0
            WHEN bss.recent_closed > 0 THEN 50.0
            ELSE 0.0
        END AS hpd_litigation_raw,
        -- building_size (uses actual unit_count now)
        CASE
            WHEN COALESCE(bss.unit_count, 0) >= 50 THEN 100.0
            WHEN COALESCE(bss.unit_count, 0) >= 20 THEN 60.0
            ELSE 20.0
        END AS building_size_raw
    FROM building_signal_summary bss
),
contributions AS (
    SELECT
        bbl,
        -- Effective weights = base + (15 * base / 85) since null_weight_pool = 15
        ownership_change_raw  * 23.529 / 100.0 AS ownership_change_contrib,
        complaint_spike_raw   * 20.000 / 100.0 AS complaint_spike_contrib,
        violation_trend_raw   * 14.118 / 100.0 AS violation_trend_contrib,
        hpd_litigation_raw    * 10.588 / 100.0 AS hpd_litigation_contrib,
        building_size_raw     *  5.882 / 100.0 AS building_size_contrib,
        -- Signals with no data → always 0
        0.0 AS dob_permits_contrib,
        0.0 AS emergency_repairs_contrib,
        0.0 AS eviction_contrib
    FROM raw_signals
),
final_scores AS (
    SELECT
        bbl,
        LEAST(100.0, GREATEST(0.0,
            ownership_change_contrib +
            complaint_spike_contrib +
            violation_trend_contrib +
            hpd_litigation_contrib +
            building_size_contrib
        )) AS churn_score,
        ROUND((
            ownership_change_contrib +
            complaint_spike_contrib +
            violation_trend_contrib +
            hpd_litigation_contrib +
            building_size_contrib
        )::numeric, 1) AS raw_score,
        complaint_spike_contrib,
        violation_trend_contrib,
        hpd_litigation_contrib,
        building_size_contrib
    FROM contributions
)
UPDATE buildings b SET
    churn_score = ROUND(fs.churn_score::numeric, 1),
    churn_category = CASE
        WHEN fs.churn_score >= 70 THEN 'hot'
        WHEN fs.churn_score >= 40 THEN 'warm'
        ELSE 'stable'
    END,
    key_signal = CASE
        WHEN GREATEST(fs.complaint_spike_contrib, fs.violation_trend_contrib,
                      fs.hpd_litigation_contrib, fs.building_size_contrib) = fs.complaint_spike_contrib
             AND fs.complaint_spike_contrib > 0 THEN 'complaint_spike'
        WHEN GREATEST(fs.complaint_spike_contrib, fs.violation_trend_contrib,
                      fs.hpd_litigation_contrib, fs.building_size_contrib) = fs.violation_trend_contrib
             AND fs.violation_trend_contrib > 0 THEN 'violation_trend'
        WHEN GREATEST(fs.complaint_spike_contrib, fs.violation_trend_contrib,
                      fs.hpd_litigation_contrib, fs.building_size_contrib) = fs.hpd_litigation_contrib
             AND fs.hpd_litigation_contrib > 0 THEN 'hpd_litigation'
        WHEN fs.building_size_contrib > 0 THEN 'building_size'
        ELSE 'none'
    END,
    signals_available = 3 + CASE WHEN COALESCE(b.unit_count, 0) > 0 THEN 1 ELSE 0 END,
    coverage_ratio = (3.0 + CASE WHEN COALESCE(b.unit_count, 0) > 0 THEN 1.0 ELSE 0.0 END) / 10.0,
    scoring_config_id = (SELECT id FROM scoring_configs WHERE is_active = true LIMIT 1),
    last_scored_at = now(),
    updated_at = now()
FROM final_scores fs
WHERE b.bbl = fs.bbl;

-- ── 4. Check score distribution ──────────────────────────────

SELECT churn_category, COUNT(*) as buildings
FROM buildings
WHERE churn_score IS NOT NULL
GROUP BY churn_category
ORDER BY 1;

SELECT
    CASE
        WHEN churn_score < 5  THEN '0-5'
        WHEN churn_score < 10 THEN '5-10'
        WHEN churn_score < 15 THEN '10-15'
        WHEN churn_score < 20 THEN '15-20'
        WHEN churn_score < 25 THEN '20-25'
        WHEN churn_score < 30 THEN '25-30'
        WHEN churn_score < 40 THEN '30-40'
        WHEN churn_score < 50 THEN '40-50'
        ELSE '50+'
    END AS bucket,
    COUNT(*) as cnt
FROM buildings
WHERE churn_score IS NOT NULL
GROUP BY bucket
ORDER BY MIN(churn_score);

COMMIT;
