#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

EXCLUDED_DIR_NAMES = {".git", ".godot", ".idea", ".vs", ".vscode", "bin", "obj"}

READONLY_WRAPPER_FILES = (
    "--z__ReadOnlyArray.cs",
    "--z__ReadOnlyList.cs",
    "--z__ReadOnlySingleElementList.cs",
)

PSEUDO_CTOR_TARGETS = (
    "src/Core/Combat/History/Entries/StarsModifiedEntry.cs",
    "src/Core/Combat/History/Entries/SummonedEntry.cs",
    "src/Core/Combat/History/Entries/EnergySpentEntry.cs",
)

ALLOWLIST_TARGETS = (
    "src/Core/Saves/SaveBatchScope.cs",
    "src/Core/CardSelection/CardSelectorPrefs.cs",
    "src/Core/Entities/Ancients/AncientDialogueSet.cs",
    "src/Core/Saves/JsonSerializationUtility.cs",
    "src/Core/Models/Encounters/BattlewornDummyEventEncounter.cs",
)

Transform = Callable[[str], str]

PRIVATE_IMPLEMENTATION_DETAILS_REQUIRED_USINGS = (
    "System",
    "System.Runtime.CompilerServices",
    "System.Runtime.InteropServices",
)

PRIVATE_IMPLEMENTATION_DETAILS_INLINE_ARRAY_ELEMENT_REF = """    public static ref TElement InlineArrayElementRef<TBuffer, TElement>(ref TBuffer buffer, int index)
        where TBuffer : struct
    {
        return ref Unsafe.Add(ref Unsafe.As<TBuffer, TElement>(ref buffer), index);
    }"""

PRIVATE_IMPLEMENTATION_DETAILS_INLINE_ARRAY_AS_READ_ONLY_SPAN = """    public static ReadOnlySpan<TElement> InlineArrayAsReadOnlySpan<TBuffer, TElement>(in TBuffer buffer, int length)
        where TBuffer : struct
    {
        return MemoryMarshal.CreateReadOnlySpan(
            ref Unsafe.As<TBuffer, TElement>(ref Unsafe.AsRef(in buffer)),
            length);
    }"""

FIXED_BATTLEWORN_GENERATE_MONSTERS = """\tprotected override IReadOnlyList<(MonsterModel, string?)> GenerateMonsters()
\t{
\t\tMonsterModel monster = Setting switch
\t\t{
\t\t\tDummySetting.Setting1 => (MonsterModel)ModelDb.Monster<BattleFriendV1>(),
\t\t\tDummySetting.Setting2 => (MonsterModel)ModelDb.Monster<BattleFriendV2>(),
\t\t\tDummySetting.Setting3 => (MonsterModel)ModelDb.Monster<BattleFriendV3>(),
\t\t\t_ => throw new InvalidOperationException("Setting must be set!"),
\t\t};
\t\t(MonsterModel, string?) item = (monster.ToMutable(), null);
\t\treturn new global::_003C_003Ez__ReadOnlySingleElementList<(MonsterModel, string?)>(item);
\t}"""


@dataclass
class FixRecord:
    path: str
    reason: str


class RepairContext:
    def __init__(self, project_dir: Path, dry_run: bool) -> None:
        self.project_dir = project_dir
        self.dry_run = dry_run
        self.records: list[FixRecord] = []
        self.warnings: list[str] = []

    def add_record(self, path: Path, reason: str) -> None:
        self.records.append(FixRecord(relative_path(path, self.project_dir), reason))

    def add_warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


def relative_path(path: Path, project_dir: Path) -> str:
    return path.resolve().relative_to(project_dir.resolve()).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def normalize_text(text: str, newline: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").replace("\n", newline) + newline


def write_text(path: Path, text: str, context: RepairContext, reason: str) -> bool:
    old = read_text(path) if path.exists() else ""
    newline = detect_newline(old) if old else "\r\n"
    new = normalize_text(text, newline)
    if old == new:
        return False
    if not context.dry_run:
        path.write_text(new, encoding="utf-8")
    context.add_record(path, reason)
    return True


def rewrite_text(path: Path, context: RepairContext, reason: str, transform: Transform) -> bool:
    if not path.exists():
        return False
    old = read_text(path)
    new = transform(old)
    if new == old:
        return False
    if not context.dry_run:
        path.write_text(new, encoding="utf-8")
    context.add_record(path, reason)
    return True


def iter_cs_files(project_dir: Path):
    for path in project_dir.rglob("*.cs"):
        parent_names = {part.lower() for part in path.relative_to(project_dir).parts[:-1]}
        if parent_names & EXCLUDED_DIR_NAMES:
            continue
        yield path


def iter_project_files(project_dir: Path, pattern: str) -> Iterable[Path]:
    for path in project_dir.rglob(pattern):
        parent_names = {part.lower() for part in path.relative_to(project_dir).parts[:-1]}
        if parent_names & EXCLUDED_DIR_NAMES:
            continue
        yield path


def run_command(command: list[str], cwd: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        return 127, f"Command not found: {command[0]}"
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip()).strip()
    return result.returncode, output


def looks_like_godot_csharp_project(project_dir: Path) -> bool:
    if (project_dir / "project.godot").exists():
        return True
    return any(project_dir.glob("*.csproj"))


def find_matching_brace(text: str, opening_brace_index: int) -> int:
    depth = 0
    for index in range(opening_brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def ensure_using_directives(text: str, namespaces: tuple[str, ...]) -> str:
    lines = text.splitlines()
    existing = {line.strip() for line in lines if line.strip().startswith("using ")}
    missing = [f"using {namespace};" for namespace in namespaces if f"using {namespace};" not in existing]
    if not missing:
        return text

    insert_index = 0
    while insert_index < len(lines) and (lines[insert_index].strip() == "" or lines[insert_index].strip().startswith("using ")):
        insert_index += 1

    new_lines = list(lines)
    new_lines[insert_index:insert_index] = missing + [""]
    return "\n".join(new_lines)


def build_private_implementation_details_class(methods: list[str]) -> str:
    joined_methods = "\n\n".join(methods)
    return f"""internal static class _003CPrivateImplementationDetails_003E
{{
{joined_methods}
}}"""


def ensure_partial_class(text: str) -> str:
    pattern = re.compile(r"^(\s*(?:public|internal|private|protected|file)\s+(?:static\s+|sealed\s+|abstract\s+|readonly\s+|unsafe\s+)*)class\b", re.MULTILINE)

    def repl(match: re.Match[str]) -> str:
        line = match.group(0)
        if " partial class" in line:
            return line
        return line.replace("class", "partial class", 1)

    return pattern.sub(repl, text, count=1)


def fix_generated_regex(path: Path, context: RepairContext) -> bool:
    text = read_text(path)
    if "[GeneratedRegex(" not in text:
        return False

    original = text
    text = ensure_partial_class(text)
    lines = text.splitlines()
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("[GeneratedRegex("):
            attribute_lines = [line]
            index += 1
            while index < len(lines):
                current = lines[index]
                current_stripped = current.strip()
                if current_stripped.startswith("["):
                    if "GeneratedCode(\"System.Text.RegularExpressions.Generator\"" not in current_stripped:
                        attribute_lines.append(current)
                    index += 1
                    continue
                if current_stripped == "":
                    index += 1
                    continue
                break
            if index >= len(lines):
                output.extend(attribute_lines)
                break

            signature_lines: list[str] = []
            while index < len(lines):
                current = lines[index]
                signature_lines.append(current)
                index += 1
                current_stripped = current.strip()
                if current_stripped.endswith(";") or "{" in current:
                    break

            signature_text = " ".join(part.strip() for part in signature_lines)
            signature_start = signature_lines[0]
            if "partial Regex" in signature_text and signature_text.endswith(");"):
                output.extend(attribute_lines)
                output.extend(signature_lines)
                continue

            name_match = re.search(r"Regex\s+([A-Za-z_]\w*)\s*\(", signature_text)
            sig_match = re.match(r"^(\s*)(.*)Regex\s+([A-Za-z_]\w*)\s*\(", signature_start)
            if not name_match or not sig_match:
                output.extend(attribute_lines)
                output.extend(signature_lines)
                continue

            indent = sig_match.group(1)
            prefix = sig_match.group(2).strip()
            if "partial" not in prefix.split():
                prefix = f"{prefix} partial".strip()
            output.extend(attribute_lines)
            output.append(f"{indent}{prefix} Regex {name_match.group(1)}();")

            brace_balance = sum(current.count("{") - current.count("}") for current in signature_lines)
            body_started = any("{" in current for current in signature_lines)
            while index < len(lines):
                current = lines[index]
                brace_balance += current.count("{") - current.count("}")
                if "{" in current:
                    body_started = True
                index += 1
                if body_started and brace_balance <= 0:
                    break
            continue

        output.append(line)
        index += 1

    return write_text(path, "\n".join(output), context, "Fix GeneratedRegex decompilation artifacts") if "\n".join(output) != original else False


def fix_serializer_context(path: Path, context: RepairContext) -> bool:
    if not path.name.endswith("SerializerContext.cs"):
        return False
    text = read_text(path)
    if "JsonSerializerContext" not in text or "[JsonSerializable(" not in text:
        return False

    lines = text.splitlines()
    class_index = next((i for i, line in enumerate(lines) if "class " in line and "JsonSerializerContext" in line), None)
    if class_index is None:
        return False

    match = re.search(r"\b(public|internal|private|protected|file)?\s*(?:sealed\s+|abstract\s+|partial\s+)*class\s+([A-Za-z_]\w*)\s*:\s*JsonSerializerContext", lines[class_index])
    if not match:
        return False

    access = match.group(1) or "internal"
    class_name = match.group(2)
    if "GeneratedCode(" not in text and re.search(rf"\bpartial class {class_name}\s*:\s*JsonSerializerContext\s*\{{\s*\}}", text, flags=re.DOTALL):
        return False

    attr_index = class_index
    while attr_index > 0:
        previous = lines[attr_index - 1].lstrip()
        if previous.startswith("[") or previous == "":
            attr_index -= 1
        else:
            break

    preamble = lines[:attr_index]
    while preamble and not preamble[-1].strip():
        preamble.pop()

    attrs: list[str] = []
    for i in range(attr_index, class_index):
        line = lines[i]
        if "GeneratedCode(" in line:
            continue
        if "JsonSourceGenerationOptions" in line or "JsonSerializable(" in line or attrs:
            attrs.append(line)

    while attrs and not attrs[-1].strip():
        attrs.pop()

    rebuilt = []
    rebuilt.extend(preamble)
    if rebuilt:
        rebuilt.append("")
    rebuilt.extend(attrs)
    if rebuilt and rebuilt[-1] != "":
        rebuilt.append("")
    rebuilt.append(f"{access} partial class {class_name} : JsonSerializerContext")
    rebuilt.append("{")
    rebuilt.append("}")

    return write_text(path, "\n".join(rebuilt), context, "Shrink JsonSerializerContext to a minimal compilable form")


def fix_inline_array(path: Path, context: RepairContext) -> bool:
    if not re.fullmatch(r"--y__InlineArray\d+\.cs", path.name):
        return False
    text = read_text(path)
    if "_element0" in text:
        return False

    struct_match = re.search(r"\b(?:internal|public)\s+struct\s+[A-Za-z0-9_<>]+\s*(?:where[^{]+)?\{", text)
    if not struct_match:
        return False

    brace_index = text.find("{", struct_match.start())
    end_index = find_matching_brace(text, brace_index)
    if end_index == -1:
        return False

    insertion = "\n    private T _element0;\n"
    new_text = text[:brace_index + 1] + insertion + text[brace_index + 1:]
    return write_text(path, new_text, context, "Add the missing InlineArray backing field")


def fix_private_implementation_details(path: Path, context: RepairContext) -> bool:
    if path.name != "-PrivateImplementationDetails-.cs":
        return False
    text = read_text(path) if path.exists() else ""
    has_element_ref = "InlineArrayElementRef" in text
    has_read_only_span = "InlineArrayAsReadOnlySpan" in text
    if has_element_ref and has_read_only_span:
        return False

    if not text.strip():
        methods = []
        if not has_element_ref:
            methods.append(PRIVATE_IMPLEMENTATION_DETAILS_INLINE_ARRAY_ELEMENT_REF)
        if not has_read_only_span:
            methods.append(PRIVATE_IMPLEMENTATION_DETAILS_INLINE_ARRAY_AS_READ_ONLY_SPAN)
        file_text = "\n".join(f"using {namespace};" for namespace in PRIVATE_IMPLEMENTATION_DETAILS_REQUIRED_USINGS)
        file_text += "\n\n" + build_private_implementation_details_class(methods)
        return write_text(path, file_text, context, "Restore a minimal PrivateImplementationDetails implementation")

    new_text = ensure_using_directives(text, PRIVATE_IMPLEMENTATION_DETAILS_REQUIRED_USINGS)
    class_match = re.search(r"\b(?:internal|public|private|protected|file)?\s*static\s+class\s+_003CPrivateImplementationDetails_003E\b[^{]*\{", new_text)

    missing_methods: list[str] = []
    if not has_element_ref:
        missing_methods.append(PRIVATE_IMPLEMENTATION_DETAILS_INLINE_ARRAY_ELEMENT_REF)
    if not has_read_only_span:
        missing_methods.append(PRIVATE_IMPLEMENTATION_DETAILS_INLINE_ARRAY_AS_READ_ONLY_SPAN)

    if class_match:
        brace_index = new_text.find("{", class_match.start())
        end_index = find_matching_brace(new_text, brace_index)
        if end_index == -1:
            return False
        injection = "\n" + "\n\n".join(missing_methods) + "\n"
        new_text = new_text[:end_index] + injection + new_text[end_index:]
    else:
        addition = build_private_implementation_details_class(missing_methods)
        new_text = new_text.rstrip() + "\n\n" + addition + "\n"

    return write_text(path, new_text, context, "Inject missing PrivateImplementationDetails members")


def fix_readonly_wrappers(path: Path, context: RepairContext) -> bool:
    def transform(text: str) -> str:
        newline = "\r\n" if "\r\n" in text else "\n"
        if path.name == "--z__ReadOnlyArray.cs" and "private readonly T[] _items;" not in text:
            return re.sub(r"\{\r?\n", "{%s\tprivate readonly T[] _items;%s%s" % (newline, newline, newline), text, count=1)
        if path.name == "--z__ReadOnlyList.cs" and "private readonly List<T> _items;" not in text:
            return re.sub(r"\{\r?\n", "{%s\tprivate readonly List<T> _items;%s%s" % (newline, newline, newline), text, count=1)
        if path.name == "--z__ReadOnlySingleElementList.cs":
            if not re.search(r"private sealed class Enumerator[\s\S]*?^\t\tprivate readonly T _item;\s*$", text, flags=re.MULTILINE):
                text = re.sub(
                    r"(private sealed class Enumerator\s*:\s*IDisposable,\s*IEnumerator,\s*IEnumerator<T>\s*\{\r?\n)",
                    r"\1\t\tprivate readonly T _item;" + newline + newline,
                    text,
                    count=1,
                )
            if not re.search(r"private sealed class Enumerator[\s\S]*?^\t\tprivate bool _moveNextCalled;\s*$", text, flags=re.MULTILINE):
                text = re.sub(
                    r"(private sealed class Enumerator[\s\S]*?^\t\tprivate readonly T _item;\s*\r?\n\r?\n)",
                    r"\1\t\tprivate bool _moveNextCalled;" + newline + newline,
                    text,
                    count=1,
                    flags=re.MULTILINE,
                )
            if not re.search(r"^\tprivate readonly T _item;\s*$", text, flags=re.MULTILINE):
                text = re.sub(
                    r"(\r?\n\r?\n\tint ICollection\.Count => 1;)",
                    newline + newline + "\tprivate readonly T _item;" + newline + r"\1",
                    text,
                    count=1,
                )
            return text
        return text

    return rewrite_text(path, context, "Restore missing readonly wrapper backing fields", transform)


def replace_constructor(text: str, class_name: str, snippet: str) -> str:
    pattern = re.compile(rf"public {class_name}\([^)]*\)\s*\{{.*?^\t\}}", re.DOTALL | re.MULTILINE)
    return pattern.sub(snippet, text, count=1)


def fix_noarg_pseudo_ctor(path: Path, context: RepairContext) -> bool:
    return rewrite_text(
        path,
        context,
        "Remove parameterless base._002Ector() pseudo-calls",
        lambda text: re.sub(r"^[ \t]*base\._002Ector\(\);\r?\n?", "", text, flags=re.MULTILINE),
    )


def fix_known_pseudo_ctor_files(path: Path, context: RepairContext) -> bool:
    rel = path.resolve().relative_to(context.project_dir.resolve()).as_posix()

    def transform(text: str) -> str:
        if rel.endswith("StarsModifiedEntry.cs"):
            return replace_constructor(text, "StarsModifiedEntry", """public StarsModifiedEntry(int amount, Player player, int roundNumber, CombatSide currentSide, CombatHistory history)
\t\t: base(player.Creature, roundNumber, currentSide, history)
\t{
\t\tAmount = amount;
\t}""")
        if rel.endswith("SummonedEntry.cs"):
            return replace_constructor(text, "SummonedEntry", """public SummonedEntry(int amount, Player player, int roundNumber, CombatSide currentSide, CombatHistory history)
\t\t: base(player.Creature, roundNumber, currentSide, history)
\t{
\t\tAmount = amount;
\t}""")
        if rel.endswith("EnergySpentEntry.cs"):
            return replace_constructor(text, "EnergySpentEntry", """public EnergySpentEntry(int amount, Player player, int roundNumber, CombatSide currentSide, CombatHistory history)
\t\t: base(player.Creature, roundNumber, currentSide, history)
\t{
\t\tAmount = amount;
\t}""")
        return text

    return rewrite_text(path, context, "Fix base._002Ector pseudo-constructor calls", transform)


def fix_known_allowlist_files(path: Path, context: RepairContext) -> bool:
    rel = path.resolve().relative_to(context.project_dir.resolve()).as_posix()

    def transform(text: str) -> str:
        if rel == "src/Core/Saves/SaveBatchScope.cs":
            if "private readonly SaveManager _saveManager;" not in text:
                text = re.sub(r"(public readonly struct SaveBatchScope\s*:\s*IDisposable\s*\{)", r"\1\n\tprivate readonly SaveManager _saveManager;\n", text, count=1)
            return text.replace("_003CsaveManager_003EP", "_saveManager")
        if rel == "src/Core/CardSelection/CardSelectorPrefs.cs":
            for name in ("RequireManualConfirmation", "Cancelable", "UnpoweredPreviews", "PretendCardsCanBePlayed"):
                text = re.sub(rf"(public bool {name} \{{ get; )init(; \}})", r"\1set\2", text)
            return text
        if rel == "src/Core/Entities/Ancients/AncientDialogueSet.cs":
            text = re.sub(r"public\s+(?:required\s+)?AncientDialogue\?\s+FirstVisitEverDialogue\s*\{\s*get;\s*(?:init|set);\s*\}", "public AncientDialogue? FirstVisitEverDialogue { get; set; }", text)
            text = re.sub(r"public\s+(?:required\s+)?Dictionary<string,\s*IReadOnlyList<AncientDialogue>>\s+CharacterDialogues\s*\{\s*get;\s*(?:init|set);\s*\}", "public Dictionary<string, IReadOnlyList<AncientDialogue>> CharacterDialogues { get; set; }", text)
            if "CharacterDialogues { get; set; }" in text and "= new Dictionary<string, IReadOnlyList<AncientDialogue>>();" not in text:
                text = text.replace("public Dictionary<string, IReadOnlyList<AncientDialogue>> CharacterDialogues { get; set; }", "public Dictionary<string, IReadOnlyList<AncientDialogue>> CharacterDialogues { get; set; } = new Dictionary<string, IReadOnlyList<AncientDialogue>>();")
            text = re.sub(
                r"public\s+(?:required\s+)?IReadOnlyList<AncientDialogue>\s+AgnosticDialogues\s*\{\s*get;\s*(?:init|set);\s*\}(?:\s*=\s*Array\.Empty<AncientDialogue>\(\);)?",
                "public IReadOnlyList<AncientDialogue> AgnosticDialogues { get; set; } = Array.Empty<AncientDialogue>();",
                text,
            )
            return text
        if rel == "src/Core/Saves/JsonSerializationUtility.cs":
            return text.replace("MegaCritSerializerContext.DefaultGeneratedSerializerOptions", "MegaCritSerializerContext.Default.Options")
        if rel == "src/Core/Models/Encounters/BattlewornDummyEventEncounter.cs":
            if "MonsterModel monster = Setting switch" in text:
                return text
            return re.sub(r"protected override IReadOnlyList<\(MonsterModel, string\?\)> GenerateMonsters\(\)\s*\{.*?^\t\}", FIXED_BATTLEWORN_GENERATE_MONSTERS, text, count=1, flags=re.DOTALL | re.MULTILINE)
        return text

    return rewrite_text(path, context, "Apply allowlist compatibility fixes", transform)


def fix_darv(path: Path, context: RepairContext) -> bool:
    if path.resolve().relative_to(context.project_dir.resolve()).as_posix() != "src/Core/Models/Events/Darv.cs":
        return False

    def transform(text: str) -> str:
        inline_init = re.search(
            r"private static readonly List<ValidRelicSet> _validRelicSets = new List<ValidRelicSet>\s*\{(?P<body>.*?)^\t\};",
            text,
            flags=re.DOTALL | re.MULTILINE,
        )
        if inline_init:
            replacement = "private static List<ValidRelicSet>? _validRelicSets;\n\n\tprivate static IReadOnlyList<ValidRelicSet> ValidRelicSets => _validRelicSets ??= CreateValidRelicSets();"
            body = inline_init.group("body").rstrip()
            text = text[:inline_init.start()] + replacement + text[inline_init.end():]
            if "private static List<ValidRelicSet> CreateValidRelicSets()" not in text:
                method = "\n\tprivate static List<ValidRelicSet> CreateValidRelicSets()\n\t{\n\t\treturn new List<ValidRelicSet>\n\t\t{" + body + "\n\t\t};\n\t}\n"
                text = re.sub(r"\n\}$", method + "}", text, count=1)

        text = text.replace("private static readonly List<ValidRelicSet> _validRelicSets;", "private static List<ValidRelicSet>? _validRelicSets;\n\n\tprivate static IReadOnlyList<ValidRelicSet> ValidRelicSets => _validRelicSets ??= CreateValidRelicSets();")
        if "private static List<ValidRelicSet>? _validRelicSets;" in text and "private static IReadOnlyList<ValidRelicSet> ValidRelicSets => _validRelicSets ??= CreateValidRelicSets();" not in text:
            text = text.replace("private static List<ValidRelicSet>? _validRelicSets;", "private static List<ValidRelicSet>? _validRelicSets;\n\n\tprivate static IReadOnlyList<ValidRelicSet> ValidRelicSets => _validRelicSets ??= CreateValidRelicSets();")
        text = text.replace("from r in _validRelicSets.SelectMany", "from r in ValidRelicSets.SelectMany")
        text = text.replace("from rs in _validRelicSets", "from rs in ValidRelicSets")
        match = re.search(r"static Darv\(\)\s*\{(?P<body>.*?)^\t\}", text, flags=re.DOTALL | re.MULTILINE)
        if not match:
            return text
        body = match.group("body")
        body = body.replace("_validRelicSets = new List<ValidRelicSet>(num);", "List<ValidRelicSet> list = new List<ValidRelicSet>(num);")
        body = body.replace("CollectionsMarshal.SetCount(_validRelicSets, num);", "CollectionsMarshal.SetCount(list, num);")
        body = body.replace("Span<ValidRelicSet> span = CollectionsMarshal.AsSpan(_validRelicSets);", "Span<ValidRelicSet> span = CollectionsMarshal.AsSpan(list);")
        replacement = "private static List<ValidRelicSet> CreateValidRelicSets()\n\t{" + body.rstrip() + "\n\t\treturn list;\n\t}"
        return text[:match.start()] + replacement + text[match.end():]

    return rewrite_text(path, context, "Convert Darv static initialization to lazy initialization", transform)


def scan_runtime_risks(project_dir: Path, context: RepairContext) -> None:
    for path in iter_cs_files(project_dir):
        text = read_text(path)
        if re.search(r"\bstatic\s+[A-Za-z_]\w*\s*\(\)", text) and re.search(r"\bModelDb\.[A-Za-z_]\w*<", text):
            rel = path.resolve().relative_to(project_dir.resolve()).as_posix()
            context.add_warning(f"{rel} contains a static constructor that directly accesses ModelDb.*<T>(); startup initialization order may still be risky.")


def scan_environment_risks(project_dir: Path, context: RepairContext) -> None:
    if not looks_like_godot_csharp_project(project_dir):
        return

    version_code, version_output = run_command(["dotnet", "--version"], project_dir)
    if version_code == 0:
        return

    requested = re.search(r"Requested SDK version:\s*(.+)", version_output)
    global_json = re.search(r"global\.json file:\s*(.+)", version_output)

    _, list_sdks_output = run_command(["dotnet", "--list-sdks"], project_dir)
    installed_sdks = ", ".join(line.strip() for line in list_sdks_output.splitlines() if line.strip()) or "none detected"

    message = "The current directory failed .NET SDK resolution, so Godot may be unable to load the C# project"
    if requested:
        message += f"; required: {requested.group(1).strip()}"
    if global_json:
        message += f"; global.json: {global_json.group(1).strip()}"
    message += f"; installed SDKs: {installed_sdks}"
    context.add_warning(message)


def fix_csproj_lang_version(path: Path, context: RepairContext) -> bool:
    if path.suffix.lower() != ".csproj":
        return False

    def transform(text: str) -> str:
        if "<TargetFramework>net9" not in text and "<TargetFramework>net90" not in text:
            return text

        match = re.search(r"<LangVersion>\s*([0-9.]+)\s*</LangVersion>", text)
        if not match:
            return text

        try:
            version = float(match.group(1))
        except ValueError:
            return text

        if version >= 13.0:
            return text

        return re.sub(r"<LangVersion>\s*[0-9.]+\s*</LangVersion>", "<LangVersion>13.0</LangVersion>", text, count=1)

    return rewrite_text(path, context, "Raise LangVersion to 13.0 for net9 projects", transform)


def apply_fixes(project_dir: Path, context: RepairContext) -> None:
    for path in iter_project_files(project_dir, "*.csproj"):
        fix_csproj_lang_version(path, context)

    for path in iter_cs_files(project_dir):
        fix_generated_regex(path, context)
    for path in iter_cs_files(project_dir):
        fix_serializer_context(path, context)
    for path in iter_cs_files(project_dir):
        fix_inline_array(path, context)

    private_details = project_dir / "-PrivateImplementationDetails-.cs"
    fix_private_implementation_details(private_details, context)

    for relative in READONLY_WRAPPER_FILES:
        target = project_dir / relative
        if target.exists():
            fix_readonly_wrappers(target, context)

    for path in iter_cs_files(project_dir):
        fix_noarg_pseudo_ctor(path, context)

    for relative in PSEUDO_CTOR_TARGETS:
        target = project_dir / relative
        if target.exists():
            fix_known_pseudo_ctor_files(target, context)

    for relative in ALLOWLIST_TARGETS:
        target = project_dir / relative
        if target.exists():
            fix_known_allowlist_files(target, context)

    darv = project_dir / "src/Core/Models/Events/Darv.cs"
    if darv.exists():
        fix_darv(darv, context)

    scan_runtime_risks(project_dir, context)
    scan_environment_risks(project_dir, context)


def print_summary(context: RepairContext) -> None:
    if context.records:
        print(f"Applied {len(context.records)} change(s):")
        for record in context.records:
            print(f"- {record.path}: {record.reason}")
    else:
        print("No changes were needed.")
    if context.warnings:
        print("\nWarnings:")
        for warning in context.warnings:
            print(f"- {warning}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sts2_repairer",
        description="Repair common decompilation artifacts in a Slay the Spire 2 C# project.",
    )
    parser.add_argument("project_dir", nargs="?", default=".", help="Project root directory. Defaults to the current directory.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report planned changes without writing files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.exists():
        print(f"Project directory does not exist: {project_dir}")
        return 1

    print(f"Target directory: {project_dir}")
    if args.dry_run:
        print("Dry-run mode enabled. Planned changes will be reported without writing files.")

    context = RepairContext(project_dir, args.dry_run)
    apply_fixes(project_dir, context)
    print_summary(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
