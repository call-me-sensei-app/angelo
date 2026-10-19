#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Command line for Angelo's localization tooling (no Blender needed except for ``extract``).

    python3 scripts/angelo/i18n/cli.py scan [module ...]     bypass report (default: every UI module)
    python3 scripts/angelo/i18n/cli.py scan --all             every add-on module, notices included
    python3 scripts/angelo/i18n/cli.py update                 merge angelo.pot into every language catalogue
    python3 scripts/angelo/i18n/cli.py build-arabic           write po/ar.po (shaped, visual) from src/ar.po
    python3 scripts/angelo/i18n/cli.py check [code ...]       validate catalogues (exit 1 on any error)
    python3 scripts/angelo/i18n/cli.py coverage               translated / required per language
    python3 scripts/angelo/i18n/cli.py pseudo OUTDIR          write a compiled pseudo-locale for QA
    python3 scripts/angelo/i18n/cli.py hints CODE             Blender's own wording of common UI words, one language
    python3 scripts/angelo/i18n/cli.py chunks CODE DIR        split the untranslated entries into JSON work files
    python3 scripts/angelo/i18n/cli.py apply CODE DIR         merge chunk-NN.out.json translations back (validated)

Regenerate the template first, inside the app:
    Angelo --background --factory-startup --python scripts/angelo/i18n/extract_pot.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from i18n import catalogs, languages, po, pseudo, scan, sources, workflow  # noqa: E402


def scan_modules(names=None, everything=False):
    findings = []
    for path in sorted(sources.ADDON.glob("*.py")):
        if names and path.stem not in names:
            continue
        if not names and not everything and path.stem not in sources.UI_MODULES:
            continue
        found, _ = scan.scan(path.read_text(encoding="utf-8"), path.name, net=path.stem in sources.UI_MODULES)
        findings += found
    for relative in sources.STARTUP_FILES:
        path = sources.ROOT / relative
        if not names and path.exists():
            found, _ = scan.scan(path.read_text(encoding="utf-8"), relative, net=True)
            findings += found
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("modules", nargs="*", help="module names, without .py")
    scan_parser.add_argument("--all", action="store_true")
    sub.add_parser("update")
    sub.add_parser("build-arabic")
    check_parser = sub.add_parser("check")
    check_parser.add_argument("codes", nargs="*")
    check_parser.add_argument("--warnings", action="store_true")
    sub.add_parser("coverage")
    pseudo_parser = sub.add_parser("pseudo")
    pseudo_parser.add_argument("out")
    for name in ("hints", "chunks", "apply"):
        work = sub.add_parser(name)
        work.add_argument("code")
        if name != "hints":
            work.add_argument("directory")
    args = parser.parse_args(argv)
    if args.command == "update":
        template_header, template = catalogs.load_template()
        for language in languages.LANGUAGES:
            added, retired = catalogs.update(language, template, template_header)
            print(f"{language.code:6s} +{added} untranslated, {retired} retired")
        return 0
    if args.command in ("hints", "chunks", "apply"):
        import json
        language = languages.BY_CODE[args.code]
        if args.command == "hints":
            print(json.dumps(workflow.hints(language), ensure_ascii=False, indent=1))
        elif args.command == "chunks":
            print(f"{workflow.chunks(language, args.directory)} chunk file(s) in {args.directory}")
        else:
            applied, problems = workflow.apply(language, args.directory)
            print(f"{applied} translation(s) merged")
            for problem in problems:
                print("  rejected:", problem)
            return 1 if problems else 0
        return 0
    if args.command == "build-arabic":
        print(f"po/ar.po: {catalogs.build_arabic()} entries")
        return 0
    if args.command in ("check", "coverage"):
        results = catalogs.check_all(set(getattr(args, "codes", [])) or None)
        errors = 0
        for code, (issues, missing, translated, required) in results.items():
            hard = [i for i in issues if i.severity == "error"]
            soft = [i for i in issues if i.severity == "warning"]
            errors += len(hard)
            print(f"{code:6s} {translated:5d}/{required:<5d} {100 * translated / max(required, 1):5.1f}%  {len(hard):4d} errors  {len(soft):4d} warnings")
            if args.command == "check":
                for issue in hard + (soft if args.warnings else []):
                    print("   ", issue)
        return 1 if errors else 0
    if args.command == "pseudo":
        import pathlib
        template_header, template = catalogs.load_template()
        head, entries = pseudo.build(template)
        out = pathlib.Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        po.write(str(out / "pseudo.po"), head, entries)
        catalogs.compile_catalog(out / "pseudo.po", out / "angelo.mo")
        print(f"pseudo-locale: {len(entries)} entries -> {out / 'angelo.mo'}")
        return 0
    if args.command == "scan":
        findings = scan_modules(set(args.modules) or None, args.all)
        for finding in findings:
            print(finding)
        print(f"{len(findings)} untranslated or untranslatable string(s)")
        return 1 if findings else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
