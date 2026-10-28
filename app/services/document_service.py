import re
import os
from typing import Dict, List, Any
import pypdf
import docx

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
        """Extracts plain text from PDF, DOCX, TXT, or Markdown documents."""
        ext = os.path.splitext(filename)[1].lower()
        if ext in ['.txt', '.md', '.markdown', '.json', '.xml', '.yaml', '.yml']:
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
        else:
            raise ValueError(f"Unsupported file format: {ext}. Please upload PDF, DOCX, TXT, or Markdown.")

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
