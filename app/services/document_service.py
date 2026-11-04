import re
import os
import csv
from typing import Dict, List, Any
import pypdf
import docx
import openpyxl

# Regex patterns for common sensitive municipal / GDPR PII
PATTERNS = {
    "EMAIL": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b'),
    "PHONE": re.compile(r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,5}\b'),
    "IBAN": re.compile(r'\b[A-Z]{2}[0-9]{2}(?:[ ]?[0-9]{4}){4}(?:\s?[0-9]{1,4})?\b'),
    "IPV4": re.compile(r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'),
    "GERMAN_TAX_ID": re.compile(r'\b\d{2}/\d{3}/\d{5}\b'),
    "INTERNAL_PASSWORD_LEAK": re.compile(r'(?:password|passwort|pwd|kennwort)\s*[:=]\s*([^\s,;]+)', re.IGNORECASE)
}

class DocumentService:
    @staticmethod
    def extract_text_from_file(file_path: str, filename: str) -> str:
        """Extracts plain text from PDF, DOCX, XLSX, CSV, TXT, or Markdown documents."""
        ext = os.path.splitext(filename)[1].lower()
        if ext in ['.txt', '.md', '.markdown', '.json', '.xml', '.yaml', '.yml', '.bpmn']:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        elif ext == '.pdf':
            reader = pypdf.PdfReader(file_path)
            pages = [page.extract_text() or '' for page in reader.pages]
            return "\n\n".join(pages)
        elif ext in ['.docx', '.doc']:
            doc = docx.Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n".join(paragraphs)
        elif ext in ['.xlsx', '.xlsm', '.xltx']:
            wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
            text_blocks = []
            for sheetname in wb.sheetnames:
                ws = wb[sheetname]
                sheet_lines = [f"=== Sheet: {sheetname} ==="]
                for row in ws.iter_rows(values_only=True):
                    row_vals = [str(c).strip() for c in row if c is not None and str(c).strip()]
                    if row_vals:
                        sheet_lines.append(" | ".join(row_vals))
                if len(sheet_lines) > 1:
                    text_blocks.append("\n".join(sheet_lines))
            wb.close()
            return "\n\n".join(text_blocks)
        elif ext == '.csv':
            lines = []
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.reader(f)
                for row in reader:
                    row_vals = [c.strip() for c in row if c.strip()]
                    if row_vals:
                        lines.append(" | ".join(row_vals))
            return "\n".join(lines)
        else:
            raise ValueError(f"Unsupported file format: {ext}. Please upload PDF, DOCX, XLSX, CSV, TXT, or Markdown.")

    @staticmethod
    def extract_excel_sheets(file_path: str) -> List[Dict[str, Any]]:
        """
        Extracts structured worksheets from an Excel workbook, preserving sheet names,
        headers, and verbatim cell values.
        """
        wb = openpyxl.load_workbook(file_path, data_only=True)
        sheets_data = []
        for sheetname in wb.sheetnames:
            ws = wb[sheetname]
            rows_iter = ws.iter_rows(values_only=True)
            rows = [list(r) for r in rows_iter if any(c is not None and str(c).strip() for c in r)]
            if not rows:
                continue
            headers = [str(c).strip() if c is not None else "" for c in rows[0]]
            data_rows = []
            for r in rows[1:]:
                row_dict = {}
                row_raw_list = []
                for idx, val in enumerate(r):
                    val_str = str(val).strip() if val is not None else ""
                    row_raw_list.append(val_str)
                    header_name = headers[idx] if idx < len(headers) and headers[idx] else f"col_{idx+1}"
                    row_dict[header_name] = val_str
                data_rows.append({
                    "raw_list": row_raw_list,
                    "row_dict": row_dict
                })
            sheets_data.append({
                "sheet_name": sheetname,
                "headers": headers,
                "rows": data_rows
            })
        wb.close()
        return sheets_data

    @staticmethod
    def scan_for_pii(text: str) -> Dict[str, Any]:
        """
        Scans text for sensitive data and citizen PII before sending to any LLM.
        Returns detected entities with positions and a pre-redacted text preview.
        """
        detections: List[Dict[str, Any]] = []
        redacted_text = text

        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                matched_val = match.group(0)
                # Filter out obvious false positives for phone numbers like single years (2023)
                if label == "PHONE" and (len(re.sub(r'\D', '', matched_val)) < 7 or matched_val.isdigit() and len(matched_val) <= 4):
                    continue
                detections.append({
                    "type": label,
                    "value": matched_val,
                    "start": match.start(),
                    "end": match.end()
                })

        # Perform clean redaction in text
        for item in sorted(detections, key=lambda x: x["start"], reverse=True):
            placeholder = f"[{item['type']}_REDACTED]"
            redacted_text = redacted_text[:item["start"]] + placeholder + redacted_text[item["end"]:]

        return {
            "has_pii": len(detections) > 0,
            "detected_count": len(detections),
            "detections": detections,
            "redacted_text": redacted_text
        }
