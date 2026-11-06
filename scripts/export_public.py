"""Package only reviewed public files; never archive the working directory wholesale."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
    ".gitignore",
    "Dockerfile",
    "compose.yaml",
    ".dockerignore",
    ".env.example",
    "README.md",
    "DEVELOPMENT.md",
    "DEVELOPMENT_PLAN.md",
    "requirements.txt",
    "app.py",
    "config.json",
    "rules.json",
    "examples/demo-transactions.csv",
    "scripts/generate_demo.py",
    "scripts/export_public.py",
    "static/index.html",
    "static/ai-rules.js",
    "static/ai-rules.css",
    "static/imports.js",
    "static/imports.css",
    "tests/test_public_boundaries.py",
    "tests/test_ai_rules.py",
    "tests/test_settings.py",
    "tests/test_import_formats.py",
    "tests/test_imports.py",
    "tests/test_currency_balances.py",
    "tests/test_currency_parser.py",
    "tests/test_currency_reports.py",
    "tests/test_currency_structured_imports.py",
    "tests/test_currency_text_imports.py",
    "static/plotly.min.js",
    "static/LICENSE.plotly.txt",
    "finreport/__init__.py",
    "finreport/__main__.py",
    "finreport/charts.py",
    "finreport/classifier.py",
    "finreport/currency.py",
    "finreport/ai_rules.py",
    "finreport/settings.py",
    "finreport/imports.py",
    "finreport/import_formats.py",
    "finreport/graph.py",
    "finreport/models.py",
    "finreport/networth.py",
    "finreport/parser.py",
    "finreport/report.py",
)


def main() -> None:
    # Exact paths, not recursive globs: uploads, backups, and new files stay private
    # until explicitly reviewed and added. Refuse symlinks in any path component.
    for name in PUBLIC_FILES:
        path = ROOT / name
        if not path.is_file():
            raise SystemExit(f"Missing public file: {name}")
        if any(p.is_symlink() for p in (path, *path.parents) if p != ROOT and ROOT in p.parents):
            raise SystemExit(f"Refusing symlink in public path: {name}")
    destination = ROOT / "dist" / "financialreport-public.zip"
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix(".zip.tmp")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for name in PUBLIC_FILES:
                info = ZipInfo(f"financialreport/{name}", date_time=(2024, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, (ROOT / name).read_bytes())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Created {destination.relative_to(ROOT)} ({len(PUBLIC_FILES)} public files)")
    print("Contains synthetic demo data only as prepared; re-review public files after editing them.")


if __name__ == "__main__":
    main()
