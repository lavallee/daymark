"""Read workspace events that change a project's plan in the caller's snapshot."""

import json
import sqlite3

# A project can name these entities as owners, labels or goals.
_NAMED = ("person", "principal", "label", "goal")
_FIELDS = "c.seq,e.workspace,e.operation_id,e.entity_id,e.action,e.before_data,e.after_data,e.created_at,e.actor"
_DIRECT = "e.entity_id IN (SELECT id FROM related)"
_STATES = ("e.before_data", "e.after_data")

# Keep related IDs inside SQLite. Binding a variable per external dependency
# hits the host's variable limit on larger plans. A project's goal and the
# current dependencies of its members can change the plan from outside it.
_QUERY = """
    WITH related(id) AS (
        SELECT :project
        UNION ALL
        SELECT d.target_id FROM daymark_refs p JOIN daymark_refs d ON
            d.workspace=p.workspace AND d.source_id=p.source_id AND d.field='depends_on'
        WHERE p.workspace=:workspace AND p.field='project_id' AND p.target_id=:project
        UNION ALL
        SELECT r.target_id FROM daymark_refs r JOIN daymark_entities p ON
            p.workspace=r.workspace AND p.id=r.source_id AND p.kind='project'
        WHERE r.workspace=:workspace AND r.source_id=:project AND r.field='goal_id'
    )
    SELECT {projection} FROM daymark_event_cursors c JOIN daymark_events e ON e.seq=c.event_seq
    WHERE c.workspace=:workspace AND c.seq>:after AND ({direct} OR ({matching_state}))
    ORDER BY c.seq
"""


def read(store, workspace, project, after, limit, *, cursor_only=False):
    projection = "c.seq" if cursor_only else _FIELDS
    names = ",".join(f"'{kind}'" for kind in _NAMED)
    matches = " OR ".join(
        f"json_extract({state},'$.project_id')=:project OR json_extract({state},'$.kind') IN ({names})"
        for state in _STATES
    )
    params = {"workspace": workspace, "project": project, "after": after, "limit": limit}
    sql = _QUERY.format(projection=projection, direct=_DIRECT, matching_state=matches)
    try:
        return store.db.execute(sql + " LIMIT :limit", params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such function: json_extract" not in str(exc):
            raise
        return _without_json(store, params, cursor_only)


def _without_json(store, params, cursor_only):
    # Nested imported metadata can contain identical keys. Prefilter in SQL,
    # then verify the candidate's actual top-level fields before counting it.
    needles = [
        '"project_id":' + json.dumps(params["project"], ensure_ascii=False),
        *(f'"kind":"{kind}"' for kind in _NAMED),
    ]
    candidates = " OR ".join(
        f"instr({state},:n{i})>0" for state in _STATES for i in range(len(needles))
    )
    sql = _QUERY.format(
        projection=f"{_FIELDS},{_DIRECT}", direct=_DIRECT, matching_state=candidates
    )
    params.update({f"n{i}": value for i, value in enumerate(needles)})
    found = []
    for row in store.db.execute(sql, params):
        if not row[-1]:
            states = (json.loads(raw) for raw in row[5:7] if raw)
            if not any(
                state.get("project_id") == params["project"] or state.get("kind") in _NAMED
                for state in states
            ):
                continue
        found.append(row[:1] if cursor_only else row[:-1])
        if len(found) == params["limit"]:
            break
    return found
