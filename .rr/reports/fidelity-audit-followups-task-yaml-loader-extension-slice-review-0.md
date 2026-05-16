# Slice Review: fidelity-audit-followups task=yaml-loader-extension

## Summary

**Verdict:** PASS  
**Cycle:** 0  
**Task:** yaml-loader-extension  
**Owns:** AT-3, AT-4, AT-6, AT-7  
**Files changed:** `claude_crew/subagents/_loader.py`, `claude_crew/subagents/_user_loader.py`, `tests/test_user_loader.py`

---

## Check 1: Slice Adherence

All four acceptance tests owned by this task are covered by newly-added test classes and pass.

| AT | Acceptance Criterion | Test Class | Result |
|----|----------------------|------------|--------|
| AT-3 | `discover_dir` returns all three canonical keys (`.md`, `.yaml`, `.yml`) | `TestYamlDiscovery` (4 cases: mixed dir, description/body loaded, both extensions, uppercase ignored) | PASS |
| AT-4 | Malformed YAML emits WARN, skips file, siblings still load | `TestYamlMalformed` (3 cases: missing `description`, missing `prompt_body`, invalid YAML syntax) | PASS |
| AT-6 | Markdown discovery non-regression: `load_default_pack()` byte-equivalent output | `TestMarkdownNonRegression` (4 cases: standard keys, descriptions, bodies, md-only dir) | PASS |
| AT-7 | Cross-format kebab collision: WARN names both paths, alphabetically-later (`.yaml`) wins | `TestYamlKebabCollision` (2 cases: `.yaml > .md`, `.yml > .md`) | PASS |

Implementation matches spec architecture:

- `parse_yaml_pack_file` added to `_loader.py` — entire YAML doc is the frontmatter mapping; `prompt_body` is the body field; delegates to pre-existing `_validate_frontmatter` + `AgentDefinition` construction path. `PackLoadError` on missing/empty `prompt_body` with clear diagnostic naming the expected field. ✓
- `strict_parse` in `_user_loader.py` extended with suffix dispatch (`.yaml`/`.yml` → YAML branch, default → markdown). Extras check fires against `_ACCEPTED_FRONTMATTER_KEYS` for both paths. ✓
- `discover_dir` glob extended via `itertools.chain(directory.glob("*.md"), directory.glob("*.yaml"), directory.glob("*.yml"))`. README exclusion, size/count caps, alphabetical sort, and collision logic all preserved across the combined set. ✓
- `parse_yaml_pack_file` imported into `_user_loader.py` and exported via `__all__` update. ✓

Edge cases from spec implemented:
- `prompt` (instead of `prompt_body`) → `PackLoadError` naming expected field ✓
- Empty `prompt_body` → `PackLoadError` ✓
- Uppercase `.YAML`/`.YML` silently skipped (lowercase-only glob) ✓
- Cross-format collision (`probe.md` + `probe.yaml`) → WARN naming both paths, later alphabetically wins ✓

_None identified at Critical or High._

---

## Check 2: Non-regression

**Command:** `uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py tests/test_user_loader.py -v`  
**Exit code:** 0  
**Result:** 79 passed / 9 skipped / 1 xfailed  

Matches build report exactly (79/9/1). No previously-green tests flipped red. Test suite is stable.

_None identified._

---

## Check 3: Code-quality Smoke

### Critical

_None identified._

### High

_None identified._

### Medium

- [Medium-01] `slice.quality.style` — `_user_loader.py::strict_parse` YAML branch (lines ~90-112): The YAML branch reads the file, parses it into `doc`, builds `fm_dict`, checks extras — then delegates to `parse_yaml_pack_file(path)` which reads and parses the file a second time. The markdown branch (pre-existing) avoids this by passing already-read `text` to `parse_pack_text(text, path)`. Double file I/O is asymmetric and unnecessary. No correctness impact; negligible cost on small agent files; but the inconsistency is a latent maintenance trap if either branch is later extended. Fix: factor out a `_parse_yaml_doc(path) -> tuple[dict, str]` helper (or pass the already-parsed `doc` into a dict-accepting sibling of `parse_yaml_pack_file`). Category: `fix-style`.

### Low

_None identified._

### Info

- [Info-01] `slice.review-process.cross-slice-observation` — `tests/test_user_loader.py` contains pre-existing inline imports inside test bodies in `TestBuildMergedPack`, `TestSettingSources`, and `TestWarnShadowDrop` (e.g., lines 400, 428, 456, 533, 556, 575, 595, 619, 1076, 1090, 1104, 1121, 1141, 1158). The new test classes added by this task (`TestYamlDiscovery`, `TestYamlMalformed`, `TestMarkdownNonRegression`, `TestYamlKebabCollision`) do not introduce any new inline imports — they import at module top. The pre-existing violations predate this slice. Feature reviewer may surface this as a low-priority cleanup item; they are not attributable to this task.

---

## Coordinator-specified style smells

| Smell | New violations in changed files? |
|-------|----------------------------------|
| Inline imports inside test bodies | No new violations. Pre-existing occurrences in `test_user_loader.py` are not attributable to this task; new test classes use module-top imports. |
| `asyncio.get_event_loop()` inside coroutines | No async code in changed files. N/A. |
| Unbounded async-iterator drains | No async code in changed files. N/A. |

---
