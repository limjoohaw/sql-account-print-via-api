"""Doc type definitions and template catalog management."""

import json
import os
from dataclasses import dataclass
from typing import Optional
from config import settings


@dataclass
class DocType:
    key: str          # e.g. "sales_invoice"
    label: str        # e.g. "Sales Invoice"
    resource: str     # API endpoint e.g. "salesinvoice"
    report_type: str  # Report Designer category e.g. "Sales Invoice"
    filename: str     # Download pattern e.g. "IV_{docno}.pdf"


def load_doc_types() -> dict[str, DocType]:
    """Load doc type definitions from doc_types.json."""
    path = settings.doc_types_path
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        key: DocType(key=key, **val)
        for key, val in data.items()
    }


def load_default_templates() -> dict[str, list[dict]]:
    """Load the built-in template catalog from default_templates.json.

    Returns:
        dict mapping Report Type name → list of {"name": str, "engine": str, "built_in": bool}
    """
    path = settings.default_templates_path
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_templates_for_doc_type(
    doc_type: DocType,
    company_templates: Optional[dict] = None,
    default_catalog: Optional[dict] = None,
) -> list[dict]:
    """Get merged template list for a doc type.

    Merges company-specific templates (from companies.json upload/manual add)
    with defaults from the built-in catalog. Company templates take priority.

    Returns:
        list of {"name": str, "engine": str} dicts for the Format dropdown
    """
    if default_catalog is None:
        default_catalog = load_default_templates()

    templates = []
    seen_names = set()

    # Company-specific templates first (from uploaded Excel or manual add)
    if company_templates and doc_type.key in company_templates:
        for t in company_templates[doc_type.key]:
            if isinstance(t, str):
                # Legacy format: just the name string
                templates.append({"name": t, "engine": "?"})
                seen_names.add(t)
            elif isinstance(t, dict):
                templates.append(t)
                seen_names.add(t["name"])

    # Fall back to default catalog if company has no uploaded templates
    if not templates:
        for t in default_catalog.get(doc_type.report_type, []):
            if t["name"] not in seen_names:
                templates.append(t)
                seen_names.add(t["name"])

    return templates


def parse_report_designer_excel(file_content: bytes) -> dict[str, list[dict]]:
    """Parse an uploaded Report Designer Excel export into templates dict.

    Args:
        file_content: Raw bytes of the .xlsx file

    Returns:
        dict mapping doc_type key → list of {"name": str, "engine": str, "built_in": bool}
        Keyed by report_type (not doc_type key) since that's how the Excel categorizes.
    """
    import io
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
    ws = wb.active

    templates_by_report_type: dict[str, list[dict]] = {}

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 6:
            continue
        rpt_name = str(row[1]).strip() if row[1] else ""
        rpt_type = str(row[2]).strip() if row[2] else ""
        builtin_raw = str(row[4]).strip() if row[4] else ""
        engine_raw = str(row[5]).strip() if row[5] else ""

        if not rpt_name or not rpt_type:
            continue

        # Engine mapping: 'o' = RTM, blank = FR3
        engine = "RTM" if engine_raw == "o" else "FR3"
        is_builtin = builtin_raw == "True"

        if rpt_type not in templates_by_report_type:
            templates_by_report_type[rpt_type] = []

        templates_by_report_type[rpt_type].append({
            "name": rpt_name,
            "engine": engine,
            "built_in": is_builtin,
        })

    return templates_by_report_type


def convert_uploaded_templates(
    uploaded: dict[str, list[dict]],
    doc_types: dict[str, DocType],
) -> dict[str, list[dict]]:
    """Convert report_type-keyed templates to doc_type key-keyed templates.

    The uploaded Excel uses Report Type names (e.g. "Sales Invoice"),
    but companies.json stores by doc_type key (e.g. "sales_invoice").
    """
    # Build reverse map: report_type → doc_type key
    rt_to_key = {dt.report_type: dt.key for dt in doc_types.values()}

    result: dict[str, list[dict]] = {}
    for report_type, tpl_list in uploaded.items():
        doc_key = rt_to_key.get(report_type)
        if doc_key:
            result[doc_key] = tpl_list

    return result
