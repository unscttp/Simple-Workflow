"""Check a saved survey: sections, citation numbering and reference recency.

Usage: check_survey.py <path to a .md or .docx survey> [--year YYYY]

Prints a JSON report and always exits 0 when the file could be read, so the
assistant sees the findings. ``passed`` is true when nothing needs fixing.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

# Each required section, matched by English or Chinese heading keywords.
SECTIONS = {
    "abstract": ("abstract", "摘要"),
    "introduction": ("introduction", "引言", "绪论", "简介"),
    "method": ("method", "methodology", "方法"),
    "taxonomy": ("taxonomy", "classification", "categor", "分类", "体系"),
    "comparison": ("comparison", "compar", "比较", "对比"),
    "challenges": ("challenge", "open problem", "挑战", "问题"),
    "future": ("future", "未来", "展望"),
    "conclusion": ("conclusion", "结论", "总结"),
    "references": ("references", "bibliography", "参考文献"),
}
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
CITATION = re.compile(r"\[(\d+(?:\s*[-–,，]\s*\d+)*)\]")
ENTRY = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$")
YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")
URL = re.compile(r"https?://\S+")
RECENT_YEARS = 3


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".md":
        return path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".docx":
        from docx import Document

        document = Document(str(path))
        lines = []
        for paragraph in document.paragraphs:
            style = paragraph.style.name if paragraph.style is not None else ""
            level = style.removeprefix("Heading ").strip()
            prefix = (
                "#" * int(level) + " " if style.startswith("Heading") and level.isdigit() else ""
            )
            lines.append(prefix + paragraph.text)
        return "\n".join(lines)
    raise ValueError("Only .md and .docx surveys can be checked.")


def numbers(group: str) -> set[int]:
    found = set()
    for part in re.split(r"\s*[,，]\s*", group):
        if re.search(r"[-–]", part):
            start, end = (int(n) for n in re.split(r"\s*[-–]\s*", part))
            if 0 < end - start <= 50:
                found.update(range(start, end + 1))
                continue
        found.update(int(n) for n in re.findall(r"\d+", part))
    return found


def check(text: str, current_year: int) -> dict:
    lines = text.splitlines()
    headings = [(i, m.group(2)) for i, line in enumerate(lines) if (m := HEADING.match(line))]
    found = {
        section: any(any(k in title.lower() for k in keys) for _, title in headings)
        for section, keys in SECTIONS.items()
    }

    reference_start = next(
        (i for i, title in headings if any(k in title.lower() for k in SECTIONS["references"])),
        None,
    )
    body = lines if reference_start is None else lines[:reference_start]
    references = [] if reference_start is None else lines[reference_start + 1 :]

    order: list[int] = []
    for line in body:
        for group in CITATION.findall(line):
            for number in sorted(numbers(group)):
                if number not in order:
                    order.append(number)
    cited = set(order)

    entries: dict[int, str] = {}
    duplicates = []
    for line in references:
        match = ENTRY.match(line)
        if match:
            number = int(match.group(1) or match.group(2))
            if number in entries:
                duplicates.append(number)
            entries[number] = match.group(3)

    years = [int(m.group(1)) for entry in entries.values() if (m := YEAR.search(entry))]
    recent = [year for year in years if year >= current_year - RECENT_YEARS + 1]
    words = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)) + len(re.findall(r"[一-鿿]", text))
    first_use_in_order = order == sorted(order)

    problems = []
    missing_sections = [name for name, ok in found.items() if not ok]
    if missing_sections:
        problems.append(f"Missing sections: {', '.join(missing_sections)}.")
    if not entries:
        problems.append("No numbered reference list found under a References heading.")
    if cited - set(entries):
        problems.append(f"Cited but not listed: {sorted(cited - set(entries))}.")
    if set(entries) - cited:
        problems.append(f"Listed but never cited: {sorted(set(entries) - cited)}.")
    if duplicates:
        problems.append(f"Reference numbers listed twice: {sorted(set(duplicates))}.")
    if entries and not first_use_in_order:
        problems.append("Citations are not numbered in order of first use.")
    without_url = sorted(n for n, entry in entries.items() if not URL.search(entry))
    if without_url:
        problems.append(f"References without a URL: {without_url}.")
    if not any(line.lstrip().startswith("|") and "---" in line for line in lines):
        problems.append("No Markdown comparison table found.")

    return {
        "passed": not problems,
        "problems": problems,
        "sections": found,
        "words": words,
        "citations": {"distinct_cited": len(cited), "first_use_in_order": first_use_in_order},
        "references": {
            "count": len(entries),
            "with_year": len(years),
            "recent_share": round(len(recent) / len(years), 2) if years else None,
            "recent_window": f"{current_year - RECENT_YEARS + 1}-{current_year}",
            "unverified": sum("[unverified" in entry for entry in entries.values()),
            "without_url": without_url,
        },
    }


def main(argv: list[str]) -> int:
    args = list(argv)
    year = date.today().year
    if "--year" in args:
        index = args.index("--year")
        year = int(args[index + 1])
        del args[index : index + 2]
    if len(args) != 1:
        print(json.dumps({"error": "Usage: check_survey.py <survey.md|survey.docx> [--year YYYY]"}))
        return 2
    path = Path(args[0])
    try:
        text = read_text(path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 2
    print(json.dumps(check(text, year), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
