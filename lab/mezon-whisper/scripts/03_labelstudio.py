"""Move labelling work in and out of Label Studio (plan 6).

  export   candidates_*.jsonl -> Label Studio task files with pre-labels:
             labelstudio/windows_test.json, windows_dev.json           (config ls_window.xml)
             labelstudio/clips_<route>.json, clips_auto_accept_sample.json (config ls_clip.xml)
  import   a Label Studio JSON export -> manifests:
             windows -> data/manifests/test.jsonl / dev.jsonl
             clips   -> data/manifests/labeled_clips.jsonl

Audio is served by Label Studio itself ("local files"): start it with
    LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true \
    LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=<project root> label-studio start
and add a Local Files storage pointing at <project root>/data in each project
(Settings -> Cloud Storage), otherwise the audio URLs are refused.

Usage:
    python scripts/03_labelstudio.py export
    python scripts/03_labelstudio.py import windows path/to/export.json
    python scripts/03_labelstudio.py import clips path/to/export.json
"""

import argparse
import json
import random
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mezon_whisper.manifest import MANIFESTS, ROOT, read_jsonl, write_jsonl  # noqa: E402

OUT = ROOT / "labelstudio"
URL_PREFIX = "/data/local-files/?d="
UNCLEAR = "[?]"
CLIP_KEEP = ("id", "audio", "duration", "speaker", "room_id", "date", "track_id",
             "offset_start", "offset_end", "source", "route", "prelabel")
WINDOW_KEEP = ("id", "split", "audio", "track_id", "room_id", "speaker", "date",
               "offset_start", "offset_end", "duration", "source")


def clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def hint(name: str, fields: dict) -> str:
    note = f"  [bị lọc: {fields['dropped']}]" if fields.get("dropped") else ""
    return f"{name}: {fields.get('raw') or '(rỗng)'}{note}"


def clip_task(record: dict) -> dict:
    prelabel = record["prelabel"]
    return {
        "data": {"audio": URL_PREFIX + record["audio"], "clip_id": record["id"], "route": record["route"],
                 "hint_a": hint("turbo", prelabel["model_a"]), "hint_b": hint("large-v3", prelabel["model_b"])},
        "predictions": [{"model_version": "prelabel", "result": [
            {"from_name": "transcription", "to_name": "audio", "type": "textarea",
             "value": {"text": [prelabel["text"]]}},
            {"from_name": "category", "to_name": "audio", "type": "choices",
             "value": {"choices": [record["category"]]}},
        ]}],
    }


def window_task(record: dict) -> dict:
    result = []
    for index, region in enumerate(record["regions"]):
        value = {"start": region["start"], "end": region["end"]}
        region_id = f"r{index:03d}"
        result += [
            {"id": region_id, "from_name": "label", "to_name": "audio", "type": "labels",
             "value": {**value, "labels": ["speech"]}},
            {"id": region_id, "from_name": "transcription", "to_name": "audio", "type": "textarea",
             "value": {**value, "text": [region["text"]]}},
            {"id": region_id, "from_name": "category", "to_name": "audio", "type": "choices",
             "value": {**value, "choices": [region["category"]]}},
        ]
    return {"data": {"audio": URL_PREFIX + record["audio"], "window_id": record["id"]},
            "predictions": [{"model_version": "prelabel", "result": result}]}


def dump(name: str, tasks: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    print(f"  {name}: {len(tasks)} tasks")


def export(args) -> int:
    windows = read_jsonl(MANIFESTS / "candidates_windows.jsonl")
    for split in ("test", "dev"):
        dump(f"windows_{split}.json", [window_task(w) for w in windows if w["split"] == split])

    clips = read_jsonl(MANIFESTS / "candidates_clips.jsonl")
    for lane in ("nonspeech_check", "human_priority", "human"):
        dump(f"clips_{lane}.json", [clip_task(c) for c in clips if c["route"] == lane])
    accepted = [c for c in clips if c["route"] == "auto_accept"]
    sample = random.Random(args.seed).sample(accepted, round(len(accepted) * args.auto_accept_sample))
    dump("clips_auto_accept_sample.json", [clip_task(c) for c in sample])
    print(f"wrote {OUT}/ (import each file into its own Label Studio project)")
    return 0


def latest_annotation(task: dict) -> dict | None:
    done = [a for a in task.get("annotations", []) if not a.get("was_cancelled")]
    return max(done, key=lambda a: a.get("updated_at") or a.get("created_at") or "") if done else None


def choices(result: list[dict], name: str) -> list[str]:
    return [c for item in result if item["from_name"] == name and "start" not in item["value"]
            for c in item["value"].get("choices", [])]


def import_windows(args) -> int:
    source = {w["id"]: w for w in read_jsonl(MANIFESTS / "candidates_windows.jsonl")}
    by_split: dict[str, list[dict]] = {"test": [], "dev": []}
    skipped = {"unlabelled": 0, "bad_audio": 0}
    for task in json.loads(Path(args.export).read_text(encoding="utf-8")):
        annotation = latest_annotation(task)
        base = source[task["data"]["window_id"]]
        if annotation is None:
            skipped["unlabelled"] += 1
            continue
        result = annotation["result"]
        status = choices(result, "status")
        if "bad_audio" in status:
            skipped["bad_audio"] += 1
            continue
        regions: dict[str, dict] = {}
        for item in result:
            if "start" not in item["value"]:
                continue
            region = regions.setdefault(item["id"], {"start": item["value"]["start"], "end": item["value"]["end"],
                                                    "text": "", "category": None})
            if item["type"] == "textarea":
                region["text"] = clean(" ".join(item["value"].get("text", [])))
            elif item["type"] == "choices":
                region["category"] = (item["value"].get("choices") or [None])[0]
        ordered = sorted(regions.values(), key=lambda r: r["start"])
        for region in ordered:
            region["start"], region["end"] = round(region["start"], 3), round(region["end"], 3)
            if not region["text"]:
                region["category"] = "nonspeech"
            elif region["category"] in (None, "nonspeech"):
                region["category"] = "vi"
            region["unclear"] = UNCLEAR in region["text"]   # excluded from WER (plan 6)
        record = {key: base[key] for key in WINDOW_KEEP}
        record.update(kind="window", regions=ordered,
                      label_type="human_reviewed" if "reviewed" in status else "human",
                      annotator=annotation.get("completed_by"))
        by_split[base["split"]].append(record)

    for split, records in by_split.items():
        if not records:
            continue
        write_jsonl(MANIFESTS / f"{split}.jsonl", records)
        reviewed = sum(r["label_type"] == "human_reviewed" for r in records)
        seconds = sum(r["duration"] for r in records)
        print(f"{split}.jsonl: {len(records)} windows, {seconds / 3600:.2f} h, {reviewed} reviewed, "
              f"{sum(len(r['regions']) for r in records)} regions")
        if split == "test" and reviewed < len(records):
            print(f"  WARNING: {len(records) - reviewed} test windows are not reviewed yet (plan 6: two passes)")
    print(f"skipped: {skipped}")
    return 0


def import_clips(args) -> int:
    source = {c["id"]: c for c in read_jsonl(MANIFESTS / "candidates_clips.jsonl")}
    path = MANIFESTS / "labeled_clips.jsonl"
    labeled = {record["id"]: record for record in read_jsonl(path)}   # later imports replace earlier ones
    skipped = {"unlabelled": 0, "bad_audio": 0, "unclear": 0}
    for task in json.loads(Path(args.export).read_text(encoding="utf-8")):
        annotation = latest_annotation(task)
        if annotation is None:
            skipped["unlabelled"] += 1
            continue
        result = annotation["result"]
        text = clean(" ".join(t for item in result if item["type"] == "textarea" for t in item["value"].get("text", [])))
        if "bad_audio" in choices(result, "flags"):
            skipped["bad_audio"] += 1
            continue
        if UNCLEAR in text:
            skipped["unclear"] += 1
            continue
        category = (choices(result, "category") or ["vi"])[0]
        if not text:
            category = "nonspeech"
        elif category == "nonspeech":
            category = "vi"
        base = source[task["data"]["clip_id"]]
        record = {key: base[key] for key in CLIP_KEEP}
        record.update(kind="clip", text=text, category=category, lang="en" if category == "en" else "vi",
                      label_type="empty_verified" if not text else "human",
                      annotator=annotation.get("completed_by"))
        labeled[record["id"]] = record

    write_jsonl(path, list(labeled.values()))
    by_category: dict[str, float] = {}
    for record in labeled.values():
        by_category[record["category"]] = by_category.get(record["category"], 0.0) + record["duration"]
    print(f"{path.name}: {len(labeled)} clips")
    for category, seconds in sorted(by_category.items()):
        print(f"  {category:10s} {seconds / 3600:6.2f} h")
    print(f"skipped in this import: {skipped}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    exp = commands.add_parser("export")
    exp.add_argument("--auto-accept-sample", type=float, default=0.05)
    exp.add_argument("--seed", type=int, default=0)
    imp = commands.add_parser("import")
    imp.add_argument("kind", choices=["windows", "clips"])
    imp.add_argument("export", help="Label Studio export file (format: JSON)")
    args = parser.parse_args()
    if args.command == "export":
        return export(args)
    return import_windows(args) if args.kind == "windows" else import_clips(args)


if __name__ == "__main__":
    sys.exit(main())
