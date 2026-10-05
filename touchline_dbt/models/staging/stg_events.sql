-- I give the raw events table clearer names and one derived clock column.
select
    event_id,
    run_id,
    match_id,
    team_id,
    event_index,
    possession_id,
    period,
    minute,
    second,
    minute * 60 + second as match_clock_seconds,
    type_name as event_type,
    outcome as event_outcome,
    actor_player_id as player_id,
    recipient_player_id,
    location_x as start_x,
    location_y as start_y,
    end_location_x as end_x,
    end_location_y as end_y
from {{ source('touchline', 'events') }}