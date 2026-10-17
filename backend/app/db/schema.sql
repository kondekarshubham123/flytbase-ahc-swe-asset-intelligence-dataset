-- Asset Intelligence Copilot — DuckDB schema
-- Raw record tables + a semantic layer. Corrections/memory are applied *inside*
-- the views so every tool, and every future question, inherits them for free.

-- ===========================================================================
-- Raw reference data
-- ===========================================================================
CREATE TABLE IF NOT EXISTS users (
    user_id     VARCHAR PRIMARY KEY,
    name        VARCHAR,
    role        VARCHAR,
    team_id     VARCHAR
);

CREATE TABLE IF NOT EXISTS teams (
    team_id       VARCHAR PRIMARY KEY,
    name          VARCHAR,
    lead_user_id  VARCHAR
);

CREATE TABLE IF NOT EXISTS areas (
    area_id     VARCHAR PRIMARY KEY,
    name        VARCHAR,
    team_id     VARCHAR,
    center_lat  DOUBLE,
    center_lon  DOUBLE
);

CREATE TABLE IF NOT EXISTS wells (
    asset_id              VARCHAR PRIMARY KEY,
    name                  VARCHAR,
    asset_category        VARCHAR,
    well_type             VARCHAR,
    area_id               VARCHAR,
    team_id               VARCHAR,
    in_charge_user_id     VARCHAR,
    lat                   DOUBLE,
    lon                   DOUBLE,
    installed_on          DATE,
    recorded_status       VARCHAR,
    last_inspection_id    VARCHAR,
    last_inspected_at     TIMESTAMP,
    last_inspector_user_id VARCHAR
);

CREATE TABLE IF NOT EXISTS equipment (
    equipment_id  VARCHAR PRIMARY KEY,
    asset_id      VARCHAR,
    kind          VARCHAR,
    model         VARCHAR
);

-- ===========================================================================
-- Records
-- ===========================================================================
CREATE TABLE IF NOT EXISTS inspections (
    inspection_id       VARCHAR PRIMARY KEY,
    asset_id            VARCHAR,
    inspected_at        TIMESTAMP,
    inspector_user_id   VARCHAR,
    method              VARCHAR,
    ambient_temp_c      DOUBLE,
    wellhead_temp_c     DOUBLE,
    spill_detected      BOOLEAN,
    spill_area_pct      DOUBLE,
    spill_est_volume_bbl DOUBLE,
    evidence_images     VARCHAR,
    notes               VARCHAR
);

CREATE TABLE IF NOT EXISTS maintenance (
    maintenance_id          VARCHAR PRIMARY KEY,
    asset_id                VARCHAR,
    started_at              TIMESTAMP,
    completed_at            TIMESTAMP,
    maintenance_type        VARCHAR,
    equipment_kind          VARCHAR,
    issue                   VARCHAR,
    action_taken            VARCHAR,
    technician_user_id      VARCHAR,
    contractor              VARCHAR,
    downtime_hours          DOUBLE,
    cost_labor              DOUBLE,
    cost_parts              DOUBLE,
    cost_total              DOUBLE,
    triggered_by_inspection_id VARCHAR,
    report_file             VARCHAR
);

CREATE TABLE IF NOT EXISTS regulatory_events (
    event_id               VARCHAR PRIMARY KEY,
    asset_id               VARCHAR,
    date                   DATE,
    agency                 VARCHAR,
    event_type             VARCHAR,
    protocol_id            VARCHAR,
    related_inspection_id  VARCHAR,
    hours_to_report        DOUBLE,
    detail                 VARCHAR,
    fine_usd               DOUBLE,
    status                 VARCHAR
);

CREATE TABLE IF NOT EXISTS notes (
    note_id        VARCHAR PRIMARY KEY,
    asset_id       VARCHAR,
    created_at     TIMESTAMP,
    author_user_id VARCHAR,
    note_type      VARCHAR,
    inspection_id  VARCHAR,
    text           VARCHAR,
    valid_from     DATE,
    valid_to       DATE
);

CREATE TABLE IF NOT EXISTS reports (
    report_file     VARCHAR PRIMARY KEY,
    asset_id        VARCHAR,
    maintenance_id  VARCHAR,
    body            VARCHAR
);

-- Facts extracted from free-text repair reports (some facts exist nowhere else).
CREATE TABLE IF NOT EXISTS report_facts (
    report_file            VARCHAR PRIMARY KEY,
    asset_id               VARCHAR,
    maintenance_id         VARCHAR,
    containment_hours      DOUBLE,
    containment_text       VARCHAR,
    spill_class            VARCHAR,
    sr01                   VARCHAR,
    sr02                   VARCHAR,
    sr03                   VARCHAR,
    sr04                   VARCHAR,
    rca_action_required    BOOLEAN,
    final_tph_pct          DOUBLE,
    soil_sample_count      INTEGER,
    reported_total_usd     DOUBLE
);

-- ===========================================================================
-- Documents (manuals + SOPs) and the rule registry
-- ===========================================================================
CREATE TABLE IF NOT EXISTS documents (
    chunk_id   VARCHAR PRIMARY KEY,
    doc_id     VARCHAR,
    title      VARCHAR,
    section    VARCHAR,
    ordinal    INTEGER,
    text       VARCHAR
);

CREATE TABLE IF NOT EXISTS rules (
    rule_id        VARCHAR PRIMARY KEY,
    scope_type     VARCHAR,   -- 'well_type' | 'protocol' | 'global'
    scope_key      VARCHAR,   -- 'T200' | 'SR-02' | '*'
    rule_key       VARCHAR,
    value_num      DOUBLE,
    value_text     VARCHAR,
    unit           VARCHAR,
    source_doc     VARCHAR,
    source_section VARCHAR,
    description    VARCHAR
);

-- ===========================================================================
-- Memory: corrections + user claims + shared knowledge
-- One append-only table covers raw-data corrections, analysis corrections,
-- shared well knowledge, well-type rules, status claims and conflicts.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS assertions (
    assertion_id       VARCHAR PRIMARY KEY,
    scope              VARCHAR,   -- inspection | well | well_type | global
    scope_key          VARCHAR,   -- INS-… | WELL-… | T200 | *
    field              VARCHAR,   -- wellhead_temp_c | spill_is_real | producing_delta_c | status_claim …
    value_text         VARCHAR,
    original_value_text VARCHAR,
    author_user_id     VARCHAR,
    reason             VARCHAR,
    status             VARCHAR,   -- confirmed | unconfirmed | superseded
    source             VARCHAR,   -- seed_note | user
    created_at         TIMESTAMP,
    valid_from         DATE,
    valid_to           DATE,
    meta               VARCHAR
);

-- ===========================================================================
-- Conversation memory (private, per user)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS sessions (
    session_id  VARCHAR PRIMARY KEY,
    user_id     VARCHAR,
    title       VARCHAR,
    created_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    message_id  VARCHAR PRIMARY KEY,
    session_id  VARCHAR,
    user_id     VARCHAR,
    role        VARCHAR,
    content     VARCHAR,
    created_at  TIMESTAMP,
    meta        VARCHAR
);

-- Wells mentioned per session; used to resolve "the wells we discussed".
CREATE TABLE IF NOT EXISTS session_focus (
    session_id  VARCHAR,
    user_id     VARCHAR,
    asset_id    VARCHAR,
    mentioned_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key   VARCHAR PRIMARY KEY,
    response    VARCHAR,
    created_at  TIMESTAMP
);

-- ===========================================================================
-- Semantic layer — corrections applied
-- ===========================================================================
CREATE OR REPLACE VIEW v_insp_corr_ranked AS
SELECT *,
       row_number() OVER (
           PARTITION BY scope_key, field
           ORDER BY (CASE source WHEN 'user' THEN 2 WHEN 'seed_note' THEN 1 ELSE 0 END) DESC,
                    created_at DESC,
                    assertion_id DESC
       ) AS rn
FROM assertions
WHERE scope = 'inspection'
  AND status = 'confirmed'
  AND field IN ('wellhead_temp_c', 'ambient_temp_c', 'spill_is_real',
                'spill_volume_bbl', 'spill_area_pct');

CREATE OR REPLACE VIEW v_insp_correction AS
SELECT scope_key AS inspection_id, field, value_text, original_value_text,
       author_user_id, reason, source
FROM v_insp_corr_ranked
WHERE rn = 1;

CREATE OR REPLACE VIEW v_inspection_resolved AS
SELECT i.*,
       COALESCE(TRY_CAST(c_wh.value_text AS DOUBLE), i.wellhead_temp_c)   AS eff_wellhead_temp_c,
       COALESCE(TRY_CAST(c_am.value_text AS DOUBLE), i.ambient_temp_c)    AS eff_ambient_temp_c,
       COALESCE(TRY_CAST(c_sr.value_text AS BOOLEAN), i.spill_detected)   AS eff_spill_real,
       COALESCE(TRY_CAST(c_vo.value_text AS DOUBLE), i.spill_est_volume_bbl) AS eff_spill_volume_bbl,
       COALESCE(TRY_CAST(c_ar.value_text AS DOUBLE), i.spill_area_pct)    AS eff_spill_area_pct,
       c_wh.author_user_id AS wh_corrected_by,
       c_sr.author_user_id AS spill_corrected_by
FROM inspections i
LEFT JOIN v_insp_correction c_wh ON c_wh.inspection_id = i.inspection_id AND c_wh.field = 'wellhead_temp_c'
LEFT JOIN v_insp_correction c_am ON c_am.inspection_id = i.inspection_id AND c_am.field = 'ambient_temp_c'
LEFT JOIN v_insp_correction c_sr ON c_sr.inspection_id = i.inspection_id AND c_sr.field = 'spill_is_real'
LEFT JOIN v_insp_correction c_vo ON c_vo.inspection_id = i.inspection_id AND c_vo.field = 'spill_volume_bbl'
LEFT JOIN v_insp_correction c_ar ON c_ar.inspection_id = i.inspection_id AND c_ar.field = 'spill_area_pct';

-- Effective rules: a confirmed well-type assertion REPLACES the manual value and
-- applies only to that scope_key, so overrides never leak to other well types.
CREATE OR REPLACE VIEW v_rule_effective AS
WITH base AS (
    SELECT scope_type, scope_key, rule_key, value_num,
           FALSE AS is_user_override, 0 AS prio
    FROM rules
),
ovr AS (
    SELECT 'well_type' AS scope_type, scope_key, field AS rule_key,
           TRY_CAST(value_text AS DOUBLE) AS value_num,
           TRUE AS is_user_override, 1 AS prio,
           row_number() OVER (PARTITION BY scope_key, field
                              ORDER BY created_at DESC) AS rn
    FROM assertions
    WHERE scope = 'well_type' AND status = 'confirmed'
),
ovr_win AS (
    SELECT scope_type, scope_key, rule_key, value_num, is_user_override, prio
    FROM ovr WHERE rn = 1
),
kept AS (
    SELECT b.scope_type, b.scope_key, b.rule_key, b.value_num, b.is_user_override, b.prio
    FROM base b
    WHERE NOT EXISTS (
        SELECT 1 FROM ovr_win o
        WHERE o.scope_key = b.scope_key AND o.rule_key = b.rule_key
    )
)
SELECT scope_type, scope_key, rule_key, value_num, is_user_override FROM kept
UNION ALL
SELECT scope_type, scope_key, rule_key, value_num, is_user_override FROM ovr_win;

CREATE OR REPLACE VIEW v_producing_threshold AS
SELECT w.well_type,
       COALESCE(
           (SELECT value_num FROM v_rule_effective r
             WHERE r.rule_key = 'producing_delta_c'
               AND (r.scope_key = w.well_type OR r.scope_key = '*')
             ORDER BY r.is_user_override DESC,
                      CASE WHEN r.scope_key = w.well_type THEN 1 ELSE 0 END DESC
             LIMIT 1),
           15.0
       ) AS threshold_c
FROM (SELECT DISTINCT well_type FROM wells) w;

-- Per-inspection production status (wellhead vs air, threshold per well type).
CREATE OR REPLACE VIEW v_prod_status AS
SELECT r.inspection_id,
       r.asset_id,
       w.well_type,
       w.area_id,
       w.team_id,
       r.inspected_at,
       r.inspector_user_id,
       r.method,
       r.eff_ambient_temp_c   AS ambient_temp_c,
       r.eff_wellhead_temp_c  AS wellhead_temp_c,
       t.threshold_c,
       (r.eff_wellhead_temp_c - r.eff_ambient_temp_c) AS delta_c,
       (r.eff_wellhead_temp_c - r.eff_ambient_temp_c) >= t.threshold_c AS producing,
       r.eff_spill_real       AS spill_detected,
       r.eff_spill_volume_bbl AS spill_volume_bbl,
       r.eff_spill_area_pct   AS spill_area_pct,
       r.wh_corrected_by,
       r.spill_corrected_by,
       r.notes
FROM v_inspection_resolved r
JOIN wells w USING (asset_id)
JOIN v_producing_threshold t ON t.well_type = w.well_type;

-- Downtime: every weekly inspection that finds the well not producing = 7 days.
CREATE OR REPLACE VIEW v_downtime AS
SELECT inspection_id, asset_id, well_type, area_id, team_id,
       inspected_at,
       wellhead_temp_c, ambient_temp_c, delta_c, threshold_c, producing,
       CASE WHEN producing THEN 0 ELSE 7 END AS downtime_days
FROM v_prod_status;

-- Valid spills only (correction-away removed), corrected volume wins.
CREATE OR REPLACE VIEW v_spills AS
SELECT p.inspection_id, p.asset_id, p.well_type, p.area_id, p.team_id,
       p.inspected_at,
       w.lat, w.lon,
       p.spill_volume_bbl AS volume_bbl,
       p.spill_area_pct,
       p.spill_corrected_by,
       (p.spill_volume_bbl >= 5.0) AS reportable,
       CASE
           WHEN p.spill_volume_bbl IS NULL THEN 'unknown'
           WHEN p.spill_volume_bbl < 1.0  THEN 'minor'
           WHEN p.spill_volume_bbl < 5.0  THEN 'moderate'
           WHEN p.spill_volume_bbl < 25.0 THEN 'reportable'
           ELSE 'major'
       END AS spill_class
FROM v_prod_status p
JOIN wells w USING (asset_id)
WHERE p.spill_detected IS TRUE;
