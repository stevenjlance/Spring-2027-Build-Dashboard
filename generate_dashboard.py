#!/usr/bin/env python3
"""
Course Build Tracker — dashboard generator.

Pulls the "Spring 2027 Course Builds" board from Asana, computes each course's
progress through the 8-stage pipeline, and writes a self-contained HTML dashboard.

A course = 8 top-level tasks that share the course name, each tagged with a "Stage"
custom field. A stage is done when its task is marked complete. The current stage is
the first unfinished stage; when all 8 are complete the course is Live. Items without
a Stage (the Curriculum-Committee entries) are ignored.

Usage:
    # Live from Asana (needs a Personal Access Token):
    export ASANA_TOKEN=xxxxxxxx
    python3 generate_dashboard.py

    # From a saved Asana tasks JSON dump (the {"data":[...]} shape):
    python3 generate_dashboard.py --from-file tasks.json

Output: course-dashboard.html next to this script.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- config
PROJECT_ID = "1217460412062915"
CF_STAGE = "1217460412062933"
CF_TYPE = "1217460412062956"
CF_CLASS = "1217460412062929"
CF_WAVE = "1217460412062942"

# Canonical pipeline order. Left = Asana stage name, right = short display label.
STAGE_ORDER = [
    ("Course Plan", "Course Plan", "plan"),
    ("Global Resources and Other Shell Setup", "Shell Setup", "plan"),
    ("Act I", "Act I", "prod"),
    ("Act II", "Act II", "prod"),
    ("Act III", "Act III", "prod"),
    ("Act IV", "Act IV", "prod"),
    ("Peer Review", "Peer Review", "review"),
    ("Baselining", "Baselining", "review"),
]
STAGE_NAMES = [s[0] for s in STAGE_ORDER]

# Shell Setup is tracked in the checklist but does NOT gate the pipeline: a course
# with its Course Plan done shows as "Act I" whether or not Shell Setup is checked.
SHELL_STAGE = "Global Resources and Other Shell Setup"
PIPELINE_NAMES = [s for s in STAGE_NAMES if s != SHELL_STAGE]

# Standards & Practices items are a separate, non-build category with a two-task path.
SP_CLASSIFICATION = "S & P"
SP_STAGE_NAMES = ["Standards & Practices", "Baselining"]

OPT_FIELDS = ",".join([
    "name", "completed", "due_on", "assignee.name",
    "custom_fields.name", "custom_fields.display_value",
])


# ---------------------------------------------------------------- data sources
def fetch_from_asana(token):
    import urllib.request
    import urllib.parse
    tasks, offset = [], None
    while True:
        params = {"project": PROJECT_ID, "opt_fields": OPT_FIELDS, "limit": 100}
        if offset:
            params["offset"] = offset
        url = "https://app.asana.com/api/1.0/tasks?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req) as resp:
            payload = json.load(resp)
        tasks.extend(payload.get("data", []))
        nxt = payload.get("next_page")
        if not nxt:
            break
        offset = nxt["offset"]
    return tasks


def load_from_file(path):
    with open(path) as fh:
        payload = json.load(fh)
    return payload["data"] if isinstance(payload, dict) else payload


# ---------------------------------------------------------------- transform
def cf(task, gid_or_name):
    for f in task.get("custom_fields", []):
        if f.get("gid") == gid_or_name or f.get("name") == gid_or_name:
            return f.get("display_value")
    return None


def build_courses(tasks):
    groups = {}
    for t in tasks:
        stage = cf(t, CF_STAGE) or cf(t, "Stage")
        if not stage:                      # skip committee / non-course items
            continue
        groups.setdefault(t["name"], []).append(t)

    courses = []
    for name, items in groups.items():
        sample = items[0]
        cls = (cf(sample, CF_CLASS) or cf(sample, "Classification") or "").strip()
        kind = "sp" if cls == SP_CLASSIFICATION else "build"
        stage_names = SP_STAGE_NAMES if kind == "sp" else STAGE_NAMES
        # Gating tasks: S&P — both tasks; build — the pipeline (Shell Setup excluded).
        pipeline_names = SP_STAGE_NAMES if kind == "sp" else PIPELINE_NAMES

        by_stage = {}
        for it in items:
            by_stage[cf(it, CF_STAGE) or cf(it, "Stage")] = it

        # Completion for every task in this kind's list (drives checklist + progress).
        stages_done = [bool(by_stage.get(s, {}).get("completed")) for s in stage_names]
        # Current stage/lead/due derive from the first unfinished gating task.
        cur_name = next((s for s in pipeline_names
                         if not by_stage.get(s, {}).get("completed")), None)
        cur_task = by_stage.get(cur_name) if cur_name else None
        lead = (cur_task or {}).get("assignee") or sample.get("assignee") or {}
        code = name.split(":")[0].strip() if ":" in name else name

        courses.append({
            "name": name,
            "code": code,
            "kind": kind,
            "type": "—" if kind == "sp" else (cf(sample, CF_TYPE) or cf(sample, "Course Type") or "—"),
            "classification": cls,
            "wave": cf(sample, CF_WAVE) or cf(sample, "Wave") or "—",
            "lead": (lead.get("name") if isinstance(lead, dict) else None) or "Unassigned",
            "stages": stages_done,
            "due": (cur_task or {}).get("due_on"),
        })

    # Builds first, then by progress desc, then name.
    courses.sort(key=lambda c: (c["kind"] != "build", -sum(c["stages"]), c["name"]))
    return courses


# ---------------------------------------------------------------- render
def render(courses, generated_iso, generated_human):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "template.html")) as fh:
        tpl = fh.read()
    return (tpl
            .replace("/*__COURSES__*/[]", json.dumps(courses, ensure_ascii=False))
            .replace("__GENERATED_ISO__", generated_iso)
            .replace("__GENERATED_HUMAN__", generated_human))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", help="Read an Asana tasks JSON dump instead of the API")
    ap.add_argument("--out", default=None, help="Output HTML path")
    args = ap.parse_args()

    if args.from_file:
        tasks = load_from_file(args.from_file)
    else:
        token = os.environ.get("ASANA_TOKEN")
        if not token:
            sys.exit("ASANA_TOKEN not set. Export a Personal Access Token, or use --from-file.")
        tasks = fetch_from_asana(token)

    courses = build_courses(tasks)
    # Embed the build instant as a UTC ISO string; the page formats it in each
    # viewer's own timezone. The Eastern string is a no-JS fallback.
    now_utc = datetime.now(ZoneInfo("UTC"))
    generated_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    generated_human = now_utc.astimezone(ZoneInfo("America/New_York")).strftime("%b %-d, %Y at %-I:%M %p %Z")
    html = render(courses, generated_iso, generated_human)

    here = os.path.dirname(os.path.abspath(__file__))
    out = args.out or os.path.join(here, "course-dashboard.html")
    out_dir = os.path.dirname(os.path.abspath(out))
    os.makedirs(out_dir, exist_ok=True)
    with open(out, "w") as fh:
        fh.write(html)

    builds = [c for c in courses if c["kind"] == "build"]
    sp = [c for c in courses if c["kind"] == "sp"]
    pipe_idx = [i for i, s in enumerate(STAGE_NAMES) if s != SHELL_STAGE]
    live = sum(1 for c in builds if all(c["stages"][i] for i in pipe_idx))
    sp_done = sum(1 for c in sp if all(c["stages"]))
    print(f"Wrote {out}")
    print(f"{len(builds)} builds ({live} live) · {len(sp)} S&P ({sp_done} complete) · updated {generated_human}")


if __name__ == "__main__":
    main()
