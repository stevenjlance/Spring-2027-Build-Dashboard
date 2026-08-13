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
        by_stage = {}
        for it in items:
            by_stage[cf(it, CF_STAGE) or cf(it, "Stage")] = it

        stages_done = [bool(by_stage.get(s, {}).get("completed")) for s in STAGE_NAMES]
        try:
            cur_idx = stages_done.index(False)
        except ValueError:
            cur_idx = None                 # all done -> Live

        cur_task = by_stage.get(STAGE_NAMES[cur_idx]) if cur_idx is not None else None
        sample = items[0]
        lead = (cur_task or {}).get("assignee") or sample.get("assignee") or {}
        code = name.split(":")[0].strip() if ":" in name else name

        courses.append({
            "name": name,
            "code": code,
            "type": cf(sample, CF_TYPE) or cf(sample, "Course Type") or "—",
            "classification": (cf(sample, CF_CLASS) or cf(sample, "Classification") or "").strip(),
            "wave": cf(sample, CF_WAVE) or cf(sample, "Wave") or "—",
            "lead": (lead.get("name") if isinstance(lead, dict) else None) or "Unassigned",
            "stages": stages_done,
            "due": (cur_task or {}).get("due_on"),
        })

    courses.sort(key=lambda c: (-sum(c["stages"]), c["name"]))
    return courses


# ---------------------------------------------------------------- render
def render(courses, generated_at):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "template.html")) as fh:
        tpl = fh.read()
    return (tpl
            .replace("/*__COURSES__*/[]", json.dumps(courses, ensure_ascii=False))
            .replace("__GENERATED_AT__", generated_at))


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
    # Always stamp in Eastern time with a label, regardless of the build server's
    # clock (Netlify builds run in UTC).
    stamp = datetime.now(ZoneInfo("America/New_York")).strftime("%b %-d, %Y at %-I:%M %p %Z")
    html = render(courses, stamp)

    here = os.path.dirname(os.path.abspath(__file__))
    out = args.out or os.path.join(here, "course-dashboard.html")
    out_dir = os.path.dirname(os.path.abspath(out))
    os.makedirs(out_dir, exist_ok=True)
    with open(out, "w") as fh:
        fh.write(html)

    done = sum(1 for c in courses if all(c["stages"]))
    print(f"Wrote {out}")
    print(f"{len(courses)} courses · {done} live · updated {stamp}")


if __name__ == "__main__":
    main()
