"""Versioned taxonomy reference tables and the legacy R/M ↔ A crosswalk (TAXONOMY_REVIEW.md §2).

Codes are not hard-coded: each taxonomy_version is a CSV in data/taxonomy/ (built from the codebook sheet by
scripts/build_taxonomy_tables.py), so a renamed or redefined category means a new table, not new code.

    taxonomy_<scheme>_<version>.csv   taxonomy_version, code, level, parent_code, name_ko, definition,
                                      include_exclude, legacy_code
    crosswalk_<from>_to_<to>.csv      from_taxonomy_version, from_code, to_taxonomy_version, to_code,
                                      mapping_type, note, reviewed_by, approval_status, approved_at

The crosswalk maps the CODE SYSTEMS only. It is reference material for human re-review; nothing here
rewrites an item's tags (work instructions: "원래 범주를 우리 범주 하나로 자동 치환하지 않는다").
Not wired into the item_tags validator yet: final field names and the safe-control representation are
pending (D3, D8, S1).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

TAXONOMY_COLUMNS = ("taxonomy_version", "code", "level", "parent_code", "name_ko", "definition",
                    "include_exclude", "legacy_code")
CROSSWALK_COLUMNS = ("from_taxonomy_version", "from_code", "to_taxonomy_version", "to_code", "mapping_type",
                     "note", "reviewed_by", "approval_status", "approved_at")
MAPPING_TYPES = ("one_to_one", "split", "merge", "no_equivalent", "needs_review")
APPROVAL_STATUSES = ("proposed", "approved", "rejected")
LEVELS = ("major", "sub")
# SemVer with an optional pre-release label (e.g. 0.9.0-draft) — the official number is decision D7.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
# major: A1, R1, M01 / sub: A1.01, R1.01, M01.01
MAJOR_RE = re.compile(r"^[A-Z]\d{1,2}$")
SUB_RE = re.compile(r"^([A-Z]\d{1,2})\.\d{2}$")


class TaxonomyError(ValueError):
    def __init__(self, source: str, problems: list[str]):
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


@dataclass(frozen=True)
class Category:
    code: str
    level: str  # major | sub
    parent_code: str  # "" for a major category
    name_ko: str = ""
    definition: str = ""
    include_exclude: str = ""
    legacy_code: str = ""

    @property
    def label(self) -> str:
        """'A1 그루밍·성적 착취' — the code-and-name form the work instructions use in tables."""
        return f"{self.code} {self.name_ko}".strip()


@dataclass
class Taxonomy:
    version: str
    categories: dict[str, Category]
    source: str = ""

    def majors(self) -> list[Category]:
        return [c for c in self.categories.values() if c.level == "major"]

    def subs(self, major: str | None = None) -> list[Category]:
        return [c for c in self.categories.values() if c.level == "sub" and major in (None, c.parent_code)]

    def has(self, code: str, level: str | None = None) -> bool:
        c = self.categories.get(code)
        return c is not None and level in (None, c.level)

    def check_pair(self, major: str, sub: str) -> str | None:
        """None if `sub` is a subcategory of `major` in this version, else the reason."""
        if not self.has(major, "major"):
            return f"{self.version}에 없는 대분류 {major!r}"
        if not self.has(sub, "sub"):
            return f"{self.version}에 없는 소분류 {sub!r}"
        if self.categories[sub].parent_code != major:
            return f"{sub}는 {self.categories[sub].parent_code} 소속 (대분류 {major}와 불일치)"
        return None

    def by_legacy(self, legacy_code: str) -> Category | None:
        return next((c for c in self.categories.values() if c.legacy_code == legacy_code), None)


# --------------------------------------------------------------------------------------------- loading

def _read(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise TaxonomyError(path.name, [f"컬럼 누락 {missing}"])
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def load_taxonomy(path: str | Path) -> Taxonomy:
    """Load one version's table and check it; raises TaxonomyError listing every problem found."""
    path = Path(path)
    rows = _read(path, TAXONOMY_COLUMNS)
    problems: list[str] = []
    versions = {r["taxonomy_version"] for r in rows}
    if len(versions) != 1:
        raise TaxonomyError(path.name, [f"한 파일에 taxonomy_version은 하나여야 함: {sorted(versions)}"])
    version = versions.pop()
    if not VERSION_RE.match(version):
        problems.append(f"taxonomy_version {version!r}이 SemVer 형식이 아님")

    cats: dict[str, Category] = {}
    legacy_seen: dict[str, str] = {}
    for i, r in enumerate(rows, 2):
        code, level, parent = r["code"], r["level"], r["parent_code"]
        where = f"{i}행 {code or '(빈 코드)'}"
        if level not in LEVELS:
            problems.append(f"{where}: level {level!r} (허용: {LEVELS})")
            continue
        if code in cats:
            problems.append(f"{where}: 코드 중복")
            continue
        if level == "major":
            if not MAJOR_RE.match(code):
                problems.append(f"{where}: 대분류 코드 형식 아님 (예: A1)")
            if parent:
                problems.append(f"{where}: 대분류인데 parent_code {parent!r}")
        else:
            m = SUB_RE.match(code)
            if not m:
                problems.append(f"{where}: 소분류 코드 형식 아님 (예: A1.01)")
            elif m.group(1) != parent:
                problems.append(f"{where}: 코드 접두어 {m.group(1)}와 parent_code {parent!r} 불일치")
        if r["legacy_code"]:
            if r["legacy_code"] in legacy_seen:
                problems.append(f"{where}: legacy_code {r['legacy_code']} 중복 ({legacy_seen[r['legacy_code']]}와)")
            legacy_seen[r["legacy_code"]] = code
        cats[code] = Category(code, level, parent, r["name_ko"], r["definition"], r["include_exclude"],
                              r["legacy_code"])

    for c in cats.values():
        if c.level == "sub" and not (c.parent_code in cats and cats[c.parent_code].level == "major"):
            problems.append(f"{c.code}: 상위 대분류 {c.parent_code!r}가 이 표에 없음")
        if c.level == "sub" and c.legacy_code and cats.get(c.parent_code) and cats[c.parent_code].legacy_code:
            legacy_parent = SUB_RE.match(c.legacy_code)
            if legacy_parent and legacy_parent.group(1) != cats[c.parent_code].legacy_code:
                problems.append(f"{c.code}: legacy {c.legacy_code}의 상위가 대분류의 legacy "
                                f"{cats[c.parent_code].legacy_code}와 다름")
    if not any(c.level == "major" for c in cats.values()):
        problems.append("대분류가 하나도 없음")
    if problems:
        raise TaxonomyError(path.name, problems)
    return Taxonomy(version, cats, source=path.name)


@dataclass(frozen=True)
class CrosswalkRow:
    from_version: str
    from_code: str
    to_version: str
    to_code: str
    mapping_type: str
    note: str = ""
    reviewed_by: str = ""
    approval_status: str = "proposed"
    approved_at: str = ""


@dataclass
class Crosswalk:
    from_version: str
    to_version: str
    rows: list[CrosswalkRow]
    source: str = ""

    def candidates(self, from_code: str) -> list[CrosswalkRow]:
        """Reference rows for one legacy code. A starting point for human re-review — not a final tag."""
        return [r for r in self.rows if r.from_code == from_code]

    def reverse(self, to_code: str) -> list[CrosswalkRow]:
        return [r for r in self.rows if r.to_code == to_code]


def load_crosswalk(path: str | Path, registry: "TaxonomyRegistry") -> Crosswalk:
    """Load a crosswalk and check every code against the two taxonomy versions it connects."""
    path = Path(path)
    rows = _read(path, CROSSWALK_COLUMNS)
    pairs = {(r["from_taxonomy_version"], r["to_taxonomy_version"]) for r in rows}
    if len(pairs) != 1:
        raise TaxonomyError(path.name, [f"한 파일에 판본 쌍은 하나여야 함: {sorted(pairs)}"])
    fv, tv = pairs.pop()
    problems: list[str] = []
    src, dst = registry.versions.get(fv), registry.versions.get(tv)
    if src is None or dst is None:
        raise TaxonomyError(path.name, [f"기준표 없음: {[v for v, t in ((fv, src), (tv, dst)) if t is None]}"])
    out, seen = [], set()
    for i, r in enumerate(rows, 2):
        cw = CrosswalkRow(fv, r["from_code"], tv, r["to_code"], r["mapping_type"], r["note"], r["reviewed_by"],
                          r["approval_status"] or "proposed", r["approved_at"])
        where = f"{i}행 {cw.from_code}→{cw.to_code or '(없음)'}"
        if (cw.from_code, cw.to_code) in seen:
            problems.append(f"{where}: 중복 행")
        seen.add((cw.from_code, cw.to_code))
        if cw.mapping_type not in MAPPING_TYPES:
            problems.append(f"{where}: mapping_type {cw.mapping_type!r} (허용: {MAPPING_TYPES})")
        if cw.approval_status not in APPROVAL_STATUSES:
            problems.append(f"{where}: approval_status {cw.approval_status!r} (허용: {APPROVAL_STATUSES})")
        if cw.approval_status == "approved" and not (cw.reviewed_by and cw.approved_at):
            problems.append(f"{where}: approved인데 reviewed_by/approved_at 공란")
        if not src.has(cw.from_code):
            problems.append(f"{where}: {fv}에 없는 코드 {cw.from_code!r}")
        if cw.mapping_type == "no_equivalent":
            if cw.to_code:
                problems.append(f"{where}: no_equivalent인데 to_code가 있음")
        elif not dst.has(cw.to_code):
            problems.append(f"{where}: {tv}에 없는 코드 {cw.to_code!r}")
        elif src.has(cw.from_code) and src.categories[cw.from_code].level != dst.categories[cw.to_code].level:
            problems.append(f"{where}: 대분류↔소분류 층위가 다름")
        out.append(cw)
    if problems:
        raise TaxonomyError(path.name, problems)
    return Crosswalk(fv, tv, out, source=path.name)


@dataclass
class TaxonomyRegistry:
    """Every taxonomy version and crosswalk in a directory (default data/taxonomy/)."""
    versions: dict[str, Taxonomy] = field(default_factory=dict)
    crosswalks: list[Crosswalk] = field(default_factory=list)

    @classmethod
    def from_dir(cls, root: str | Path) -> "TaxonomyRegistry":
        root = Path(root)
        reg = cls()
        for p in sorted(root.glob("taxonomy_*.csv")):
            t = load_taxonomy(p)
            if t.version in reg.versions:
                raise TaxonomyError(p.name, [f"taxonomy_version {t.version} 이 이미 {reg.versions[t.version].source}에 있음"])
            reg.versions[t.version] = t
        for p in sorted(root.glob("crosswalk_*.csv")):
            reg.crosswalks.append(load_crosswalk(p, reg))
        return reg

    def get(self, version: str) -> Taxonomy:
        if version not in self.versions:
            raise KeyError(f"taxonomy_version {version!r} 기준표 없음 (있는 판본: {sorted(self.versions)})")
        return self.versions[version]

    def crosswalk(self, from_version: str, to_version: str) -> Crosswalk:
        for c in self.crosswalks:
            if (c.from_version, c.to_version) == (from_version, to_version):
                return c
        raise KeyError(f"crosswalk {from_version} → {to_version} 없음")
