{% macro latest_validated_records(source_table_name) %}
select
    batch_id,
    source_primary_key,
    source_updated_at,
    payload,
    extracted_at
from (
    select
        batch_id,
        source_primary_key,
        source_updated_at,
        payload,
        extracted_at,
        row_number() over (
            partition by source_primary_key
            order by source_updated_at desc, batch_id desc
        ) as version_rank
    from {{ source('zepic_raw', 'validated_records') }}
    where source_table = '{{ source_table_name }}'
) ranked
where version_rank = 1
{% endmacro %}
