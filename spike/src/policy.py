CONFIDENCE = {'explicit_user_command': 1.0, 'explicit_user_confirmation': 1.0,
              'verified_tool_result': .95, 'authoritative_document': .95,
              'agent_inference': .7, 'imported_curated': 1.0}
def initial_state(record):
    assertion = record['source_assertion']
    proposed = record['payload'].get('decision_status') == 'proposed'
    candidate = assertion == 'agent_inference' or proposed
    return ('candidate' if candidate else 'accepted', 'pending' if candidate else ('human_approved' if assertion == 'imported_curated' else 'auto_accepted'))
def projectable(state): return state['status'] in {'accepted','superseded'} and state['projection'] != 'removed'
