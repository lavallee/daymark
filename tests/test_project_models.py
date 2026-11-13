"""Randomised model tests for projects, adapted from an independent review.

Manual order against a reference list (place, create, archive, reopen, complete a
repeating task, change project), and plan, projects() and goal roll-ups against a
naive recomputation. Deterministic seeds; all records are synthetic.
"""

import random
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from daymark import DomainError, Store, Tasks, plans

NOW = datetime(2030, 4, 3, 15, 0, tzinfo=UTC)


def run(seed, steps=300, verbose=False):
    rng = random.Random(seed)
    store = Store(":memory:")
    t = Tasks(store, "w", actor="a", timezone="UTC", now=NOW)
    projects = ["p1", "p2"]
    for p in projects:
        t.project(p, id=p)
    model = {p: [] for p in projects}  # full list incl. archived, in order
    archived, done = set(), set()
    counter = [0]
    where = {}

    def new_id():
        counter[0] += 1
        return f"t{counter[0]:05d}"

    def create(p, every=False):
        i = new_id()
        if every:
            t.add(i, id=i, project=p, every="week", plan="2030-04-03")
        else:
            t.add(i, id=i, project=p)
        model[p].append(i)
        where[i] = p
        return i

    for p in projects:
        for _ in range(4):
            create(p, every=rng.random() < 0.3)

    def visible(p):
        return [x for x in model[p] if x not in archived]

    def check(step, op):
        for p in projects:
            got = [c["id"] for c in t.plan(p)["tasks"]]
            want = visible(p)
            if got != want:
                return f"seed {seed} step {step} after {op}: project {p}\n got  {got}\n want {want}"
            for x in model[p]:
                r = store.get("w", x)["rank"]
                if r is not None and (not plans.valid_rank(r)):
                    return f"seed {seed} step {step}: invalid rank {r!r} on {x}"
            ranks = [plans.effective_rank(store.get("w", x)) for x in model[p]]
            if len(set(ranks)) != len(ranks):
                return f"seed {seed} step {step} after {op}: duplicate keys in {p}: {ranks}"
        return None

    maxlen = 0
    for step in range(steps):
        p = rng.choice(projects)
        live = visible(p)
        r = rng.random()
        op = None
        try:
            if r < 0.45 and live:
                x = rng.choice(live)
                kind = rng.choice(["after", "before", "first", "last"])
                others = [y for y in live if y != x]
                if kind in ("after", "before") and others:
                    y = rng.choice(others)
                    op = f"place {x} {kind} {y}"
                    t.move(x, **{kind: y})
                    model[p].remove(x)
                    j = model[p].index(y)
                    model[p].insert(j + 1 if kind == "after" else j, x)
                elif kind == "first":
                    op = f"place {x} first"
                    t.move(x, first=True)
                    model[p].remove(x)
                    model[p].insert(0, x)
                else:
                    op = f"place {x} last"
                    t.move(x, last=True)
                    model[p].remove(x)
                    model[p].append(x)
            elif r < 0.55:
                op = "create"
                create(p, every=rng.random() < 0.3)
            elif r < 0.63 and live:
                x = rng.choice(live)
                op = f"archive {x}"
                t.archive(x)
                archived.add(x)
            elif r < 0.70 and archived & set(model[p]):
                x = rng.choice(sorted(archived & set(model[p])))
                op = f"reopen {x}"
                t.reopen(x)
                archived.discard(x)
                done.discard(x)
            elif r < 0.82 and live:
                cand = [x for x in live if x not in done]
                if not cand:
                    continue
                x = rng.choice(cand)
                item = store.get("w", x)
                op = f"complete {x}"
                t.done(x)
                done.add(x)
                if item["recurrence"]:
                    succ = [
                        e["id"]
                        for e in store.list("w", kind="task")
                        if e.get("series_id") == (item["series_id"] or x) and e["id"] not in where
                    ]
                    for s in succ:
                        where[s] = p
                        model[p].insert(model[p].index(x) + 1, s)
            elif r < 0.88 and live:
                cand = [x for x in live if x in done]
                if not cand:
                    continue
                x = rng.choice(cand)
                op = f"reopen-done {x}"
                t.reopen(x)
                done.discard(x)
            elif live:
                x = rng.choice(live)
                q = next(o for o in projects if o != p)
                op = f"move {x} {p}->{q}"
                t.edit(x, project=q)
                model[p].remove(x)
                model[q].append(x)
                where[x] = q
        except DomainError as exc:
            return f"seed {seed} step {step} {op}: DomainError {exc.code} {exc}"
        if op is None:
            continue
        err = check(step, op)
        if err:
            return err
        for x in where:
            rk = store.get("w", x)["rank"]
            if rk:
                maxlen = max(maxlen, len(rk))
    return f"ok maxlen={maxlen}"


@pytest.mark.parametrize("seed", range(10))
def test_manual_order_matches_a_list_model(seed):
    result = run(seed)
    assert result.startswith("ok"), result


ZONES = ["UTC", "America/New_York", "Asia/Tokyo"]
STATUSES = ["open", "waiting", "completed", "cancelled", "proposed"]


def temporal(rng):
    if rng.random() < 0.35:
        return None
    zone = rng.choice(ZONES)
    day = date(2030, 4, 1) + timedelta(days=rng.randrange(-10, 40))
    if rng.random() < 0.5:
        return {"date": day.isoformat(), "timezone": zone}
    at = datetime(
        day.year, day.month, day.day, rng.choice([0, 1, 12, 23]), 30, tzinfo=ZoneInfo(zone)
    )
    return {"at": at.isoformat(), "timezone": zone}


def civil(t):
    if t is None:
        return None
    if t.get("date"):
        return t["date"]
    return datetime.fromisoformat(t["at"]).astimezone(ZoneInfo(t["timezone"])).date().isoformat()


def is_past(t, now):
    z = ZoneInfo(t["timezone"])
    if t.get("date"):
        return date.fromisoformat(t["date"]) < now.astimezone(z).date()
    return datetime.fromisoformat(t["at"]) < now


def dates(x):
    return {
        "start": civil(x["planned"] or x["earliest_start"]),
        "end": civil(x["latest_target"] or x["deadline"]),
    }


def summ(items, numfields):
    counted = [x for x in items if x["status"] != "cancelled"]
    done = sum(x["status"] == "completed" for x in counted)
    st = [dates(x)["start"] for x in items if dates(x)["start"]]
    en = [dates(x)["end"] for x in items if dates(x)["end"]]
    by = {}
    for x in items:
        by[x["status"]] = by.get(x["status"], 0) + 1
    sums = {}
    for f in numfields:
        nums = [x["values"][f] for x in counted if f in x["values"]]
        if nums:
            sums[f] = sum(nums)
    est = [x["estimate_minutes"] for x in counted if x["estimate_minutes"]]
    return {
        "total": len(items),
        "by_status": by,
        "done": done,
        "percent_done": (100 * done) // len(counted) if counted else 0,
        "estimate_minutes": sum(est) if est else None,
        "start": min(st, default=None),
        "end": max(en, default=None),
        "sums": sums,
    }


def build(seed):
    rng = random.Random(seed)
    store = Store(":memory:")
    t = Tasks(store, "w", actor="a", timezone="UTC", now=NOW)
    ex = store.execute
    cnt = [0]

    def op():
        cnt[0] += 1
        return f"op{cnt[0]}"

    ex(
        "w",
        op(),
        "create",
        {
            "id": "g",
            "kind": "goal",
            "title": "G",
            "latest_target": {"date": "2030-04-20", "timezone": "UTC"},
        },
        expected_version=0,
    )
    preset = rng.choice(["epics", "event", "simple"])
    t.project("P", id="p", preset=preset, goal="g")
    t.configure("p", fields="pts:Pts:number,cost:Cost:number,note:Note:text")
    t.project("Q", id="q", goal="g")
    ids = []
    n = rng.randrange(5, 40)
    for i in range(n):
        tid = f"t{i:03d}"
        proj = "p" if rng.random() < 0.85 else "q"
        payload = {"id": tid, "kind": "task", "title": tid, "project_id": proj}
        for f in ("planned", "latest_target", "deadline", "earliest_start"):
            v = temporal(rng)
            if v:
                payload[f] = v
        if rng.random() < 0.5:
            payload["estimate_minutes"] = rng.randrange(0, 300)
        if proj == "p":
            vals = {}
            if rng.random() < 0.6:
                vals["pts"] = rng.choice([0, 1, 2.5, -3, 8])
            if rng.random() < 0.3:
                vals["cost"] = rng.randrange(0, 1000)
            if vals:
                payload["values"] = vals
        if rng.random() < 0.2:
            payload["milestone"] = True
        if rng.random() < 0.2:
            payload["goal_id"] = "g"
        same = [x for x in ids if store.get("w", x)["project_id"] == proj]
        if same and rng.random() < 0.5:
            payload["parent_id"] = rng.choice(same)
        ms = [x for x in same if store.get("w", x)["milestone"]]
        if ms and rng.random() < 0.6:
            payload["milestone_id"] = rng.choice(ms)
        if same and rng.random() < 0.3:
            payload["depends_on"] = rng.sample(same, min(len(same), rng.randrange(1, 3)))
        status = rng.choice(STATUSES)
        if status == "proposed":
            payload["status"] = "proposed"
        try:
            ex("w", op(), "create", payload, expected_version=0)
        except DomainError:
            continue
        ids.append(tid)
        if status in ("completed", "cancelled", "waiting"):
            cur = store.get("w", tid)
            if status == "waiting":
                ex(
                    "w",
                    op(),
                    "update",
                    {"id": tid, "status": "waiting"},
                    expected_version=cur["version"],
                )
            else:
                ex(
                    "w",
                    op(),
                    "complete" if status == "completed" else "cancel",
                    {"id": tid},
                    expected_version=cur["version"],
                )
        if rng.random() < 0.1:
            cur = store.get("w", tid)
            ex("w", op(), "archive", {"id": tid}, expected_version=cur["version"])
    return store, t


def check(seed):
    store, _ = build(seed)
    out = []
    view = store.plan("w", "p", now=NOW)
    proj = store.get("w", "p")
    alltasks = [x for x in store.list("w", kind="task", include_archived=True)]
    tasks = [x for x in alltasks if x["project_id"] == "p" and not x["archived"]]
    has_cancel = any(s["status"] == "cancelled" for s in proj["stages"])
    cards = [
        x for x in tasks if x["status"] != "proposed" and (x["status"] != "cancelled" or has_cancel)
    ]
    nf = ["pts", "cost"]
    cid = {c["id"] for c in view["tasks"]}
    if cid != {c["id"] for c in cards}:
        out.append(("cards", sorted(cid), sorted(c["id"] for c in cards)))
    members = {x["id"] for x in tasks}
    # parents
    want_parents = {}
    for c in cards:
        if c["parent_id"] in members:
            want_parents.setdefault(c["parent_id"], []).append(c)
    for pid, kids in want_parents.items():
        got = view["parents"].get(pid)
        exp = summ(kids, nf)
        if got is None:
            out.append(("parent missing", pid))
            continue
        for k, v in exp.items():
            if got[k] != v:
                out.append(("parent", pid, k, got[k], v))
    if set(view["parents"]) != set(want_parents):
        out.append(("parent set", sorted(view["parents"]), sorted(want_parents)))
    # milestones
    gotm = {m["id"]: m for m in view["milestones"]}
    for m in [c for c in cards if c["milestone"]]:
        belongs = [c for c in cards if c["milestone_id"] == m["id"]]
        d = dates(m)
        when = d["end"] or d["start"]
        late = sorted(
            c["id"]
            for c in belongs
            if c["status"] in ("open", "waiting")
            and when
            and dates(c)["end"]
            and dates(c)["end"] > when
        )
        g = gotm.get(m["id"])
        if g is None:
            out.append(("milestone missing", m["id"]))
            continue
        if sorted(g["late"]) != late:
            out.append(("late", m["id"], g["late"], late))
        counted = [c for c in belongs if c["status"] != "cancelled"]
        if g["items_done"] != (bool(counted) and all(c["status"] == "completed" for c in counted)):
            out.append(("items_done", m["id"]))
        for k, v in summ(belongs, nf).items():
            if g[k] != v:
                out.append(("milestone", m["id"], k, g[k], v))
    # project rollup + health
    s = summ(cards, nf)
    r = view["rollup"]
    for k in ("total", "done", "percent_done", "estimate_minutes", "sums"):
        if r[k] != s[k]:
            out.append(("rollup", k, r[k], s[k]))
    live = [c for c in cards if c["status"] in ("open", "waiting")]
    overdue = sum(1 for c in live if c["deadline"] and is_past(c["deadline"], NOW))
    if r["overdue"] != overdue:
        out.append(("overdue", r["overdue"], overdue))
    byid = {x["id"]: x for x in alltasks}

    def eff(x):
        return None if x is None else ("cancelled" if x["archived"] else x["status"])

    blocked = sum(
        1
        for c in live
        if any(eff(byid.get(d)) not in ("completed", "cancelled") for d in c["depends_on"])
    )
    if view["health"]["blocked"] != blocked:
        out.append(("blocked", view["health"]["blocked"], blocked))
    # projects() must agree with plan()
    pv = next(x for x in store.projects("w", now=NOW)["projects"] if x["id"] == "p")
    if pv["health"] != view["health"]:
        out.append(("projects-vs-plan health", pv["health"], view["health"]))
    if pv["rollup"] != view["rollup"]:
        out.append(("projects-vs-plan rollup",))
    # goal
    gv = store.goal_plan("w", "g", now=NOW)
    items = {}
    for x in alltasks:
        if x["archived"] or x["status"] == "proposed":
            continue
        if x["project_id"] in ("p", "q") or x["goal_id"] == "g":
            items[x["id"]] = x
    items = list(items.values())
    gs = summ(items, [])
    for k in ("total", "done", "percent_done", "by_status", "estimate_minutes", "start", "end"):
        if gv["rollup"][k] != gs[k]:
            out.append(("goal", k, gv["rollup"][k], gs[k]))
    risk = sorted(
        x["id"]
        for x in items
        if x["status"] in ("open", "waiting")
        and (
            (x["deadline"] and is_past(x["deadline"], NOW))
            or (dates(x)["end"] and dates(x)["end"] > "2030-04-20")
        )
    )
    if sorted(gv["at_risk"]) != risk:
        out.append(("at_risk", sorted(gv["at_risk"]), risk))
    return out


def test_rollups_match_a_naive_recomputation():
    failures = {}
    for seed in range(200):
        for difference in check(seed):
            failures.setdefault(difference[0], (seed, difference))
    assert failures == {}
