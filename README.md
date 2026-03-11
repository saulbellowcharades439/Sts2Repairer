# sts2_repairer

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![CLI](https://img.shields.io/badge/Interface-CLI-2F855A)
![Scope](https://img.shields.io/badge/Target-StS2%20Decompiled%20Project-6B46C1)

`sts2_repairer` is a small CLI tool for repairing common C# decompilation artifacts in a Slay the Spire 2 project export.

## Requirements

- Python 3.12 or newer
- A decompiled/exported Slay the Spire 2 project directory

## Quick Start

Run the script from this repository:

```powershell
python sts2_repairer.py "C:\Path\To\Your\Project"
```

If you are already inside the target project directory:

```powershell
python "C:\Path\To\sts2_repairer.py"
```

If you want to preview changes without writing files:

```powershell
python sts2_repairer.py "C:\Path\To\Your\Project" --dry-run
```

## Usage

```text
python sts2_repairer.py [project_dir] [--dry-run]
```

### Arguments

- `project_dir`
  Project root directory. If omitted, the current directory is used.

- `--dry-run`
  Scans the project and reports planned changes without modifying files.

## Examples

Repair the current directory:

```powershell
python sts2_repairer.py
```

Repair a specific exported project:

```powershell
python sts2_repairer.py "C:\Users\YourName\Desktop\Slay the Spire 2"
```

Preview changes only:

```powershell
python sts2_repairer.py "C:\Users\YourName\Desktop\Slay the Spire 2" --dry-run
```

## Output

Typical output looks like this:

```text
Target directory: C:\Users\YourName\Desktop\Slay the Spire 2
Applied 3 change(s):
- src/Core/Helpers/StringHelper.cs: Fix GeneratedRegex decompilation artifacts
- src/Core/Entities/Ancients/AncientDialogueSet.cs: Apply allowlist compatibility fixes
- sts2.csproj: Raise LangVersion to 13.0 for net9 projects
```

If no changes are needed:

```text
Target directory: C:\Users\YourName\Desktop\Slay the Spire 2
No changes were needed.
```

If you use `--dry-run`, the script prints the same summary without writing files.

## Recommended Workflow

1. Export or decompile the project.
2. Run `sts2_repairer` on the project root.
3. Re-run with `--dry-run` if you want to confirm the project is already clean.
4. Open the repaired project in your editor or build environment.

## Notes

- Point the script at the exported project root, not at the installed game binaries folder.
- The tool is safe to run multiple times on the same project.
- Some warnings may still remain after repair. This tool focuses on common decompilation issues that block normal project recovery.

## File Location

This repository ships the tool here:

```text
sts2_repairer.py
```
