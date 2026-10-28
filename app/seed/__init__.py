import os
from app.config import settings
from app.seed.iso_27002_catalog import build_iso_excel
from app.seed.benchmark_catalog import save_benchmark_data


def ensure_initial_datasets() -> None:
    """
    Automatically ensures the 93-control ISO/IEC 27002:2022 Master Excel workbook
    and public municipal BPMN benchmark datasets exist on disk when the app starts.
    No manual one-off scripts required.
    """
    iso_excel_path = os.path.join(
        settings.DATA_DIR, "iso_checklists", "ISO_27002_2022_Master_Checklist.xlsx"
    )
    if not os.path.exists(iso_excel_path):
        build_iso_excel(iso_excel_path)

    benchmarks_dir = os.path.join(settings.DATA_DIR, "public_benchmarks")
    benchmarks_index = os.path.join(benchmarks_dir, "benchmarks_index.json")
    if not os.path.exists(benchmarks_index):
        save_benchmark_data(benchmarks_dir)
