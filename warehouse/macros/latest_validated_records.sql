{% macro latest_validated_records(source_table_name) %}
with quality_checked_versions as (
    select
        raw.batch_id,
        raw.source_primary_key,
        raw.source_updated_at,
        raw.payload,
        raw.extracted_at,
        validated.batch_id is not null as is_valid,
        row_number() over (
            partition by raw.source_primary_key
            order by raw.source_updated_at desc, raw.batch_id desc
        ) as version_rank
    from {{ source('zepic_raw', 'records') }} raw
    left join {{ source('zepic_raw', 'validated_records') }} validated
        on validated.batch_id = raw.batch_id
        and validated.source_table = raw.source_table
        and validated.source_primary_key = raw.source_primary_key
    where raw.source_table = '{{ source_table_name }}'
      and exists (
          select 1
          from {{ source('zepic_raw', 'quality_runs') }} quality_run
          where quality_run.batch_id = raw.batch_id
      )
)

select
    batch_id,
    source_primary_key,
    source_updated_at,
    payload,
    extracted_at
from quality_checked_versions
where version_rank = 1
  and is_valid
{% endmacro %}
