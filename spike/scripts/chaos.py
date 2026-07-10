#!/usr/bin/env python3
"""Manual/live chaos protocol; journal behavior remains testable without Docker."""
import json
print(json.dumps({'manual_steps':['docker compose stop falkordb','run ingest; expect projection_failed only after journal commit','docker compose start falkordb','rerun projector with same build_id; assert no duplicate deterministic edge UUIDs'], 'status':'not_run_requires_live_falkordb'},indent=2))
