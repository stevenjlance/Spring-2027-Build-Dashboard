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
CF_WAVE = "1217460412062942"  # the "Sprint" custom field (formerly "Wave"); matched by GID

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

# Pillars courses are built like Pathways but WITHOUT an Act IV task (7 tasks).
PILLARS_STAGE_NAMES = [s for s in STAGE_NAMES if s != "Act IV"]
PILLARS_PIPELINE_NAMES = [s for s in PILLARS_STAGE_NAMES if s != SHELL_STAGE]

# Standards & Practices items are a separate, non-build category with a two-task path.
SP_CLASSIFICATION = "S & P"
SP_STAGE_NAMES = ["Standards & Practices", "Baselining"]

# Revisions, QA, and Rebuilds all run a two-task path: Course Updates → Baselining.
# "Couse Refresh" is an older (misspelled) stage that means the same as "Course Updates".
REVISION_CLASSIFICATIONS = {"Course Revisions", "Revision", "QA", "Rebuild"}
REVISION_STAGE_NAMES = ["Course Updates", "Baselining"]

# Course Setup: two items per person ("CM Updates", "Problem Set Conversions"), each a
# two-task path Convert Items → Baselining. Grouped per person (item names repeat).
COURSESETUP_CLASSIFICATION = "Course Setup"
COURSESETUP_STAGE_NAMES = ["Convert Items", "Baselining"]

# Lab Setup: a single task per lab course, with no Stage — just done / not done.
LABSETUP_CLASSIFICATION = "Lab Setup"
LABSETUP_STAGE_NAMES = ["Lab Setup"]

# Some stage positions accept more than one Asana stage name.
STAGE_ALIASES = {"Course Updates": ["Course Updates", "Couse Refresh"]}

# Maps a Pillars course code to its "Video Scripts" Drive folder (see video_folders.json).
def load_video_folders():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "video_folders.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}

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


def lead_for(by_stage, cur_task, sample):
    """The Instructional Designer = whoever owns the build.

    The Peer Review task is assigned to the peer reviewer (a different person), so
    it must never decide the ID. Prefer the Course Plan owner; otherwise take the
    most common assignee across the remaining (non-Peer-Review) tasks.
    """
    def nm(task):
        a = (task or {}).get("assignee")
        return a.get("name") if isinstance(a, dict) and a else None

    cp = nm(by_stage.get("Course Plan"))
    if cp:
        return cp
    counts = {}
    for stage, it in by_stage.items():
        if stage == "Peer Review":
            continue
        who = nm(it)
        if who:
            counts[who] = counts.get(who, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return nm(cur_task) or nm(sample)


def stage_task(by_stage, stage_name):
    """Task for a stage position, honoring aliases (e.g. the old 'Couse Refresh')."""
    for n in STAGE_ALIASES.get(stage_name, [stage_name]):
        if n in by_stage:
            return by_stage[n]
    return None


def group_key(t):
    cls = (cf(t, CF_CLASS) or cf(t, "Classification") or "").strip()
    if cls == COURSESETUP_CLASSIFICATION:
        who = (t.get("assignee") or {}).get("name") or "?"
        return (t["name"], who)            # Course Setup item names repeat per person
    return (t["name"],)


def build_courses(tasks):
    groups = {}
    for t in tasks:
        stage = cf(t, CF_STAGE) or cf(t, "Stage")
        cls = (cf(t, CF_CLASS) or cf(t, "Classification") or "").strip()
        if not stage and cls != LABSETUP_CLASSIFICATION:
            continue                       # skip committee / placeholder (Lab Setup has no stage)
        groups.setdefault(group_key(t), []).append(t)

    video_folders = load_video_folders()
    STAGE_LISTS = {"pathways": STAGE_NAMES, "pillars": PILLARS_STAGE_NAMES,
                   "sp": SP_STAGE_NAMES, "revision": REVISION_STAGE_NAMES,
                   "coursesetup": COURSESETUP_STAGE_NAMES, "labsetup": LABSETUP_STAGE_NAMES}
    PIPE_LISTS = {"pathways": PIPELINE_NAMES, "pillars": PILLARS_PIPELINE_NAMES,
                  "sp": SP_STAGE_NAMES, "revision": REVISION_STAGE_NAMES,
                  "coursesetup": COURSESETUP_STAGE_NAMES, "labsetup": LABSETUP_STAGE_NAMES}
    KIND_ORDER = {"pathways": 0, "pillars": 1, "revision": 2,
                  "coursesetup": 3, "labsetup": 4, "sp": 5}

    courses = []
    for items in groups.values():
        sample = items[0]
        name = sample["name"]
        by_stage = {}
        classes = set()
        for it in items:
            st = cf(it, CF_STAGE) or cf(it, "Stage")
            if st:
                by_stage[st] = it
            c = (cf(it, CF_CLASS) or cf(it, "Classification") or "").strip()
            if c:
                classes.add(c)

        # Detect kind from the whole group (tasks can carry inconsistent classifications).
        if LABSETUP_CLASSIFICATION in classes:
            kind = "labsetup"
        elif COURSESETUP_CLASSIFICATION in classes or "Convert Items" in by_stage:
            kind = "coursesetup"
        elif SP_CLASSIFICATION in classes or "Standards & Practices" in by_stage:
            kind = "sp"
        elif (classes & REVISION_CLASSIFICATIONS) or "Course Updates" in by_stage or "Couse Refresh" in by_stage:
            kind = "revision"
        elif "Pillars" in classes:
            kind = "pillars"
        else:
            kind = "pathways"
        cls = next(iter(classes), "")
        stage_names = STAGE_LISTS[kind]
        pipeline_names = PIPE_LISTS[kind]

        if kind == "labsetup":
            # One task, no Stage: complete iff any task in the group is complete.
            done = any(it.get("completed") for it in items)
            stages_done = [bool(done)]
            cur_task = None if done else sample
        else:
            stages_done = [bool((stage_task(by_stage, s) or {}).get("completed")) for s in stage_names]
            cur_name = next((s for s in pipeline_names
                             if not (stage_task(by_stage, s) or {}).get("completed")), None)
            cur_task = stage_task(by_stage, cur_name) if cur_name else None

        lead = lead_for(by_stage, cur_task, sample)

        if kind == "coursesetup":
            code, type_label = "", "Course Setup"      # per-person item, not a course
        elif kind == "labsetup":
            code = name.split(":")[0].strip() if ":" in name else name
            type_label = "Lab Setup"
        elif kind == "sp":
            code = name.split(":")[0].strip() if ":" in name else name
            type_label = "—"
        else:
            code = name.split(":")[0].strip() if ":" in name else name
            type_label = cf(sample, CF_TYPE) or cf(sample, "Course Type") or "—"

        course = {
            "name": name,
            "code": code,
            "kind": kind,
            "type": type_label,
            "classification": cls,
            "wave": cf(sample, CF_WAVE) or cf(sample, "Sprint") or cf(sample, "Wave") or "—",
            "lead": lead or "Unassigned",
            "stages": stages_done,
            "due": (cur_task or {}).get("due_on"),
        }
        if kind == "pillars":
            course["videoFolder"] = video_folders.get(code)
        courses.append(course)

    # Pathways, then Pillars, then S&P; within each by progress desc, then name.
    courses.sort(key=lambda c: (KIND_ORDER.get(c["kind"], 9), -sum(c["stages"]), c["name"]))
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

    n = lambda k: sum(1 for c in courses if c["kind"] == k)
    print(f"Wrote {out}")
    print(f"{n('pathways')} Pathways · {n('pillars')} Pillars · {n('revision')} Revisions · "
          f"{n('coursesetup')} Course Setup · {n('labsetup')} Lab Setup · {n('sp')} S&P · "
          f"updated {generated_human}")


if __name__ == "__main__":
    main()
