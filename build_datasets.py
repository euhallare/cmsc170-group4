#!/usr/bin/env python3
"""
build_datasets.py - turn the raw AMIS JSON (made by scrape.py) into clean CSV/JSON
datasets for the CMSC 170 CSP project.  Standard library only (no pandas, no numpy).

Input  : data/raw/term_<ID>/<SUBJECT>/page_NNNN.json      (from scrape.py)
         curriculum.csv                                    (BS CS 2018 flowchart)
Output : data/datasets/term_<ID>/
    sections.csv            one row per class section (LEC or LAB)
    meetings.csv            one row per (section, day) meeting, times in minutes
    faculty/                -> dataset A: Faculty Course Load Scheduler
        assignments.csv         who teaches what right now (the real-life baseline)
        instructors.csv         per-instructor load summary
        instance.json           the CSP: variables = sections, values = instructors
    student/                -> dataset B: Student Schedule Builder
        bundles.csv             LEC+LAB "bundles" (what a student actually enlists in)
        coverage.csv            which curriculum courses exist this term
        instances.json          one CSP instance per curriculum term (Y1-FS, Y1-SS, ...)
    summary.json            counts so you can quote them on the project page

Usage:  python build_datasets.py                (term 1261)
        python build_datasets.py --term 1261 --raw data/raw --curriculum curriculum.csv
"""
import argparse
import csv
import glob
import json
import os
import re
from collections import defaultdict

DAYS = [("mon", "M"), ("tue", "T"), ("wed", "W"), ("thu", "Th"), ("fri", "F"), ("sat", "S")]
FORBIDDEN_KEYS = {"first_name", "middle_name", "last_name", "formatted_name", "user_id", "user"}


# ----------------------------------------------------------------- helpers
def parse_time(text):
    """'07:00 AM' -> minutes after midnight (420).  Returns None if unreadable."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s*$", text or "")
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if hour == 12:
        hour = 0
    if ampm == "PM":
        hour += 12
    return hour * 60 + minute


def hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def to_int(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def find_forbidden(obj, found):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS:
                found.add(k)
            find_forbidden(v, found)
    elif isinstance(obj, list):
        for x in obj:
            find_forbidden(x, found)


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)


# ----------------------------------------------------------------- loading
def load_rows(term_dir):
    """Read every page file; return {class_id: row}.  Duplicates collapse on id."""
    files = sorted(glob.glob(os.path.join(term_dir, "*", "page_*.json")))
    if not files:
        raise SystemExit(f"No page files found under {term_dir}. Run scrape.py first.")
    rows, leaked = {}, set()
    for path in files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        find_forbidden(data, leaked)
        for r in (data.get("classes") or {}).get("data") or []:
            rows[r["id"]] = r
    if leaked:
        print(f"WARNING: raw files still contain identity keys {sorted(leaked)}. "
              "They are NOT copied to the datasets, but re-run scrape.py (it scrubs them) "
              "before committing data/raw to GitHub.")
    print(f"Read {len(files)} page files -> {len(rows)} distinct class rows")
    return rows


def meetings_of(row):
    """list of (day_code, start_min, end_min) plus a flag if something was unreadable."""
    out, bad = [], False
    for cd in row.get("class_dates") or []:
        s, e = parse_time(cd.get("start_time")), parse_time(cd.get("end_time"))
        days = [code for key, code in DAYS if cd.get(key)]
        if s is None or e is None or e <= s or not days:
            bad = True
            continue
        out.extend((d, s, e) for d in days)
    return out, bad


def faculty_of(row):
    out = []
    for f in row.get("faculties") or []:
        fid = f.get("faculty_id")
        if fid is not None:
            out.append((int(fid), f.get("type") or ""))
    return out


def make_section(row, source):
    course = row.get("course") or {}
    code = row.get("course_code") or course.get("course_code") or ""
    reqs = [f"{q.get('req_type')}:{q.get('req_courses')}"
            for q in course.get("requisites") or []
            if (q.get("req_courses") or "-").strip() not in ("-", "")]
    mt, bad = meetings_of(row)
    active = to_int(row.get("active_class_size"))
    if source == "embedded_parent":      # the embedded parent's enrolment count is not reliable
        active = None
    return {
        "class_id": row["id"],
        "course_id": row.get("course_id"),
        "course_code": code,
        "subject": course.get("subject") or code.split(" ")[0],
        "course_number": course.get("course_number") or "",
        "title": course.get("title") or "",
        "type": row.get("type") or "",
        "section": row.get("section") or "",
        "parent_class_id": row.get("parent_class_id"),
        "units": to_int(course.get("units"), 0),
        "max_size": to_int(row.get("max_class_size")),
        "active_size": active,
        "facility_id": row.get("facility_id") or "",
        "sem_offered": course.get("sem_offered") or "",
        "consent": row.get("consent") or "",
        "acad_org": row.get("acad_org") or "",
        "prerequisites": "; ".join(reqs),
        "meetings": mt,
        "bad_meeting": bad,
        "faculty": faculty_of(row),
        "source": source,
    }


def build_sections(rows):
    secs = {rid: make_section(r, "row") for rid, r in rows.items()}
    # A LAB row carries its LEC inside "parent"; keep it if the LEC was not scraped itself.
    for r in rows.values():
        p = r.get("parent")
        if isinstance(p, dict) and p.get("id") not in secs:
            p = dict(p)
            p.setdefault("course", r.get("course"))
            p.setdefault("course_code", r.get("course_code"))
            secs[p["id"]] = make_section(p, "embedded_parent")
    return secs


# ----------------------------------------------------------------- shared tables
def write_sections_and_meetings(out, secs):
    hdr = ["class_id", "course_id", "course_code", "subject", "course_number", "title", "type",
           "section", "parent_class_id", "units", "max_size", "active_size", "facility_id",
           "sem_offered", "consent", "acad_org", "prerequisites", "n_meetings",
           "weekly_minutes", "source"]
    rows, mrows = [], []
    for s in sorted(secs.values(), key=lambda x: (x["course_code"], x["type"], x["section"])):
        wk = sum(e - b for _, b, e in s["meetings"])
        rows.append([s[k] for k in hdr[:17]] + [len(s["meetings"]), wk, s["source"]])
        for d, b, e in s["meetings"]:
            mrows.append([s["class_id"], d, b, e, e - b, hhmm(b), hhmm(e)])
    write_csv(os.path.join(out, "sections.csv"), hdr, rows)
    write_csv(os.path.join(out, "meetings.csv"),
              ["class_id", "day", "start_min", "end_min", "minutes", "start", "end"], mrows)


def overlap(a, b):
    """a, b are lists of (day, start, end); True if any pair collides."""
    for d1, s1, e1 in a:
        for d2, s2, e2 in b:
            if d1 == d2 and s1 < e2 and s2 < e1:
                return True
    return False


# ----------------------------------------------------------------- dataset A: faculty
def build_faculty(out, secs):
    fdir = os.path.join(out, "faculty")
    assign, load = [], defaultdict(lambda: {"classes": 0, "minutes": 0, "courses": set(), "subjects": set()})
    for s in secs.values():
        wk = sum(e - b for _, b, e in s["meetings"])
        for fid, role in s["faculty"]:
            assign.append([s["class_id"], fid, role])
            L = load[fid]
            L["classes"] += 1
            L["minutes"] += wk
            L["courses"].add(s["course_code"])
            L["subjects"].add(s["subject"])
    write_csv(os.path.join(fdir, "assignments.csv"), ["class_id", "faculty_id", "role"], sorted(assign))
    write_csv(os.path.join(fdir, "instructors.csv"),
              ["faculty_id", "n_classes", "n_courses", "weekly_minutes", "subjects"],
              [[fid, L["classes"], len(L["courses"]), L["minutes"], "|".join(sorted(L["subjects"]))]
               for fid, L in sorted(load.items())])

    by_subject = defaultdict(set)          # who is "eligible" for a subject = already teaches it
    for fid, L in load.items():
        for sub in L["subjects"]:
            by_subject[sub].add(fid)

    variables = []
    for s in sorted(secs.values(), key=lambda x: x["class_id"]):
        if not s["meetings"]:
            continue                        # unscheduled (TBA) sections cannot clash
        variables.append({
            "class_id": s["class_id"], "course_code": s["course_code"], "type": s["type"],
            "section": s["section"], "subject": s["subject"],
            "meetings": [[d, b, e] for d, b, e in s["meetings"]],
            "weekly_minutes": sum(e - b for _, b, e in s["meetings"]),
            "baseline_faculty": [fid for fid, _ in s["faculty"]],
            "domain": sorted(by_subject[s["subject"]]),
        })
    mins = [L["minutes"] for L in load.values()]
    write_json(os.path.join(fdir, "instance.json"), {
        "description": ("Faculty Course Load CSP. Variables = sections; value = faculty_id from "
                        "'domain'. Hard: one instructor cannot teach two overlapping sections. "
                        "Soft: balance weekly_minutes across instructors. "
                        "baseline_faculty is the real assignment (use as a reference, not as truth)."),
        "n_variables": len(variables), "n_instructors": len(load),
        "mean_weekly_minutes": (sum(mins) / len(mins)) if mins else 0,
        "variables": variables,
    })
    base_conf = 0                           # conflicts already present in the real assignment
    per = defaultdict(list)
    for v in variables:
        for fid in v["baseline_faculty"]:
            per[fid].append(v["meetings"])
    for lists in per.values():
        for i in range(len(lists)):
            for j in range(i + 1, len(lists)):
                if overlap([tuple(m) for m in lists[i]], [tuple(m) for m in lists[j]]):
                    base_conf += 1
    return {"faculty_variables": len(variables), "faculty_instructors": len(load),
            "faculty_baseline_conflicts": base_conf}


# ----------------------------------------------------------------- dataset B: student
def build_bundles(secs):
    by_course = defaultdict(list)
    for s in secs.values():
        by_course[s["course_code"]].append(s)
    bundles = []
    for code, lst in sorted(by_course.items()):
        ids = {s["class_id"] for s in lst}
        children = defaultdict(list)
        for s in lst:
            if s["parent_class_id"] in ids:
                children[s["parent_class_id"]].append(s)
        for s in sorted(lst, key=lambda x: (x["section"], x["class_id"])):
            if s["parent_class_id"] in ids:
                continue                    # handled through its parent
            kids = children.get(s["class_id"])
            parts = [[s, k] for k in sorted(kids, key=lambda x: x["section"])] if kids else [[s]]
            if not kids and s["parent_class_id"] is not None:
                parts = [[s]]               # LAB whose LEC was not found: stands alone
            bundles.extend(parts)
    out = []
    for i, comps in enumerate(bundles, 1):
        meet = [m for c in comps for m in c["meetings"]]
        seats = [c["max_size"] - c["active_size"] for c in comps
                 if c["max_size"] is not None and c["active_size"] is not None]
        selfc = any(overlap(comps[a]["meetings"], comps[b]["meetings"])
                    for a in range(len(comps)) for b in range(a + 1, len(comps)))
        out.append({
            "bundle_id": f"B{i:05d}", "course_code": comps[0]["course_code"],
            "label": "+".join(c["section"] for c in comps),
            "lec_class_id": comps[0]["class_id"],
            "lab_class_id": comps[1]["class_id"] if len(comps) > 1 else "",
            "units": comps[0]["units"], "open_seats": max(0, min(seats)) if seats else "",
            "meetings": meet, "tba": not meet or any(c["bad_meeting"] for c in comps),
            "self_conflict": selfc,
            "sem_offered": comps[0]["sem_offered"], "prerequisites": comps[0]["prerequisites"],
        })
    return out


def load_curriculum(path):
    if not os.path.exists(path):
        print(f"(curriculum file {path} not found - skipping student instances)")
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_student(out, secs, curriculum):
    sdir = os.path.join(out, "student")
    bundles = build_bundles(secs)
    write_csv(os.path.join(sdir, "bundles.csv"),
              ["bundle_id", "course_code", "label", "lec_class_id", "lab_class_id", "units",
               "open_seats", "n_meetings", "weekly_minutes", "tba", "self_conflict", "meetings",
               "sem_offered", "prerequisites"],
              [[b["bundle_id"], b["course_code"], b["label"], b["lec_class_id"], b["lab_class_id"],
                b["units"], b["open_seats"], len(b["meetings"]),
                sum(e - s for _, s, e in b["meetings"]), b["tba"], b["self_conflict"],
                ";".join(f"{d} {hhmm(s)}-{hhmm(e)}" for d, s, e in b["meetings"]),
                b["sem_offered"], b["prerequisites"]] for b in bundles])

    usable = defaultdict(list)
    count = defaultdict(int)
    for b in bundles:
        count[b["course_code"]] += 1
        if not b["tba"] and not b["self_conflict"]:
            usable[b["course_code"]].append(b)

    cov, instances = {}, []
    groups = defaultdict(list)
    for row in curriculum:
        groups[(row["year"], row["term"])].append(row)
    for (year, term), slots in groups.items():
        tracks = sorted({s["track"] for s in slots if s["track"] != "ALL"}) or ["ALL"]
        for tr in tracks:
            variables, missing = [], []
            for s in slots:
                if s["track"] not in ("ALL", tr):
                    continue
                opts = [o.strip().upper() for o in s["options"].split("|") if o.strip()]
                if s["kind"] in ("GE", "ELECTIVE") or not opts:
                    continue                # open choice slots are not fixed variables
                for o in opts:
                    cov.setdefault(o, count.get(o, 0))
                dom = [b["bundle_id"] for o in opts for b in usable.get(o, [])]
                entry = {"slot": s["slot"], "options": opts, "prereqs": s["prereqs"], "domain": dom}
                (variables if dom else missing).append(entry)
            instances.append({
                "instance_id": f"Y{year}-{term}" + ("" if tr == "ALL" else f"-{tr}"),
                "year": int(year), "term": term, "track": tr,
                "variables": variables, "unavailable_slots": missing,
            })
    instances.sort(key=lambda x: (x["year"], x["term"], x["track"]))
    write_csv(os.path.join(sdir, "coverage.csv"), ["course_code", "n_bundles_in_term"],
              sorted(cov.items()))
    meet = {b["bundle_id"]: b["meetings"] for b in bundles}
    write_json(os.path.join(sdir, "instances.json"), {
        "description": ("Student schedule CSP, one instance per curriculum term. Variables = "
                        "curriculum slots; value = bundle_id from 'domain'; hard constraint = no two "
                        "chosen bundles overlap. Meetings of every bundle are in 'bundle_meetings'."),
        "bundle_meetings": {k: [[d, s, e] for d, s, e in v] for k, v in meet.items()},
        "instances": instances,
    })
    return {"student_bundles": len(bundles), "student_usable_bundles": sum(len(v) for v in usable.values()),
            "student_instances": len(instances),
            "curriculum_options_without_offering": sorted(k for k, v in cov.items() if v == 0)}


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--term", type=int, default=1261)
    ap.add_argument("--raw", default=os.path.join("data", "raw"))
    ap.add_argument("--out", default=os.path.join("data", "datasets"))
    ap.add_argument("--curriculum", default="curriculum.csv")
    args = ap.parse_args()

    rows = load_rows(os.path.join(args.raw, f"term_{args.term}"))
    secs = build_sections(rows)
    out = os.path.join(args.out, f"term_{args.term}")
    write_sections_and_meetings(out, secs)

    summary = {"term_id": args.term, "n_sections": len(secs),
               "n_lec": sum(1 for s in secs.values() if s["parent_class_id"] is None),
               "n_lab": sum(1 for s in secs.values() if s["parent_class_id"] is not None),
               "n_without_meetings": sum(1 for s in secs.values() if not s["meetings"]),
               "subjects": sorted({s["subject"] for s in secs.values()})}
    summary.update(build_faculty(out, secs))
    summary.update(build_student(out, secs, load_curriculum(args.curriculum)))
    write_json(os.path.join(out, "summary.json"), summary)

    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print(f"\nDatasets written to {out}")


if __name__ == "__main__":
    main()
