#!/usr/bin/env python3
"""检索本机已安装的 skill，按项目方向打分，输出可留证的候选清单。

用法：
    python3 scripts/find_skills.py -q 产业 -q 财务 --out data/skill_scan.json

扫描范围（目录存在才扫描）：
    当前项目的 .trae/skills、~/.trae-cn/skills、~/.trae/skills、~/.claude/skills
    以及插件目录 ~/.trae-cn/plugins（递归查找 SKILL.md）

只读：不改动任何被扫描的文件。
退出码：0 有候选；1 无候选；2 参数错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

MAX_DEPTH = 7
BODY_SCAN_LINES = 200
SELF_NAME = "content-pipeline-builder"
WEIGHT = {"name": 6, "description": 3, "body": 1}


def collect_roots(cwd: Path, extra: list[str], include_plugins: bool) -> list[tuple[str, Path]]:
    candidates = [
        ("project", cwd / ".trae" / "skills"),
        ("user-trae", Path.home() / ".trae-cn" / "skills"),
        ("user-trae-legacy", Path.home() / ".trae" / "skills"),
        ("user-claude", Path.home() / ".claude" / "skills"),
    ]
    if include_plugins:
        candidates.append(("plugins", Path.home() / ".trae-cn" / "plugins"))
    candidates.extend(("custom", Path(p).expanduser()) for p in extra)

    seen: set[Path] = set()
    found: list[tuple[str, Path]] = []
    for label, path in candidates:
        if not path.is_dir():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append((label, resolved))
    return found


def iter_skill_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel_depth = len(current.relative_to(root).parts)
        if rel_depth >= MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
        if "SKILL.md" in filenames:
            yield current / "SKILL.md"


def parse_skill(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    lines = text.splitlines()
    name = path.parent.name
    description = ""
    if lines and lines[0].strip() == "---":
        for line in lines[1:40]:
            if line.strip() == "---":
                break
            match = re.match(r"^(name|description)\s*:\s*(.+)$", line)
            if not match:
                continue
            value = match.group(2).strip().strip('"').strip("'")
            if match.group(1) == "name":
                name = value
            else:
                description = value
    body = "\n".join(lines[:BODY_SCAN_LINES]).lower()
    return {"name": name, "description": description, "path": str(path), "body": body}


def score_skill(skill: dict, terms: list[str]) -> tuple[int, list[str]]:
    total = 0
    hits: list[str] = []
    name = skill["name"].lower()
    description = skill["description"].lower()
    body = skill["body"]
    for term in terms:
        needle = term.lower()
        best = 0
        where = ""
        if needle in name:
            best, where = WEIGHT["name"], "name"
        elif needle in description:
            best, where = WEIGHT["description"], "description"
        elif needle in body:
            best, where = WEIGHT["body"], "body"
        if best:
            total += best
            hits.append(f"{term}@{where}")
    return total, hits


def main() -> int:
    parser = argparse.ArgumentParser(description="检索本机 skill 并打分")
    parser.add_argument("-q", "--query", action="append", required=True, help="检索词，可重复")
    parser.add_argument("--out", help="候选清单输出路径（JSON）")
    parser.add_argument("--root", action="append", default=[], help="追加扫描目录")
    parser.add_argument("--top", type=int, default=20, help="最多列出多少个候选")
    parser.add_argument("--cwd", default=".", help="项目根目录，默认当前目录")
    parser.add_argument("--no-plugins", action="store_true", help="不扫描插件目录")
    parser.add_argument("--keep-self", action="store_true", help="保留本 skill 自身")
    args = parser.parse_args()

    terms = [t.strip() for t in args.query if t.strip()]
    too_short = [t for t in terms if len(t) < 2]
    if too_short:
        print(f"忽略过短检索词（单字噪声太大）：{', '.join(too_short)}", file=sys.stderr)
    terms = [t for t in terms if len(t) >= 2]
    if not terms:
        print("没有有效检索词（每个词至少 2 个字符）", file=sys.stderr)
        return 2

    cwd = Path(args.cwd).expanduser().resolve()
    roots = collect_roots(cwd, args.root, not args.no_plugins)
    if not roots:
        print("未找到任何 skill 目录，请用 --root 指定", file=sys.stderr)
        return 1

    scanned = 0
    seen_names: set[str] = set()
    candidates: list[dict] = []
    for label, root in roots:
        for skill_file in iter_skill_files(root):
            skill = parse_skill(skill_file)
            if not skill:
                continue
            if not args.keep_self and skill["name"] == SELF_NAME:
                continue
            if skill["name"] in seen_names:
                continue
            seen_names.add(skill["name"])
            scanned += 1
            score, hits = score_skill(skill, terms)
            if score <= 0:
                continue
            candidates.append({
                "name": skill["name"],
                "description": skill["description"],
                "path": skill["path"],
                "source": label,
                "score": score,
                "hits": hits,
            })

    candidates.sort(key=lambda item: (-item["score"], item["name"]))
    shown = candidates[: args.top]

    print(f"扫描目录：{', '.join(f'{label}={root}' for label, root in roots)}")
    print(f"扫描 SKILL.md：{scanned} 个；命中：{len(candidates)} 个")
    if shown:
        print("")
        print(f"{'分数':<5}{'名称':<34}{'来源':<12}描述")
        for item in shown:
            desc = item["description"][:70] or "(无描述)"
            print(f"{item['score']:<5}{item['name']:<34}{item['source']:<12}{desc}")
        print("")
        print("命中词：" + "；".join(f"{i['name']}={','.join(i['hits'])}" for i in shown))
    else:
        print("没有命中。放宽检索词，或改用语义检索再补一轮。")

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "query": terms,
        "roots": [{"source": label, "path": str(root)} for label, root in roots],
        "scanned": scanned,
        "matched": len(candidates),
        "candidates": shown,
    }

    if args.out:
        out_path = Path(args.out).expanduser()
        if not out_path.is_absolute():
            out_path = cwd / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
        print("")
        print(f"输出文件：{out_path}")
        print(f"sha256：{digest}")
    else:
        print("")
        print(f"sha256(清单)：{hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}")

    return 0 if candidates else 1


if __name__ == "__main__":
    sys.exit(main())
