

WITH source_data AS (
    SELECT
        unique_id,
        accessed_at
    FROM
        "airflow"."lead_raw"."raw_batch_data"
)

SELECT
    *
FROM
    source_data