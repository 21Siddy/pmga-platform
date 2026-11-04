import pytest
from app.services.iso_excel_service import ISOExcelService

def test_load_master_checklist_contains_93_controls():
    controls = ISOExcelService.load_master_checklist()
    assert len(controls) == 93, f"Expected 93 controls from ISO 27002:2022, found {len(controls)}"

    # Check distribution across chapters
    ch5 = [c for c in controls if "5." in c["chapter"]]
    ch6 = [c for c in controls if "6." in c["chapter"]]
    ch7 = [c for c in controls if "7." in c["chapter"]]
    ch8 = [c for c in controls if "8." in c["chapter"]]

    assert len(ch5) == 37, f"Expected 37 controls in Chapter 5 (Organizational), got {len(ch5)}"
    assert len(ch6) == 8, f"Expected 8 controls in Chapter 6 (People), got {len(ch6)}"
    assert len(ch7) == 14, f"Expected 14 controls in Chapter 7 (Physical), got {len(ch7)}"
    assert len(ch8) == 34, f"Expected 34 controls in Chapter 8 (Technological), got {len(ch8)}"

def test_control_fields_populated():
    controls = ISOExcelService.load_master_checklist()
    c = next(x for x in controls if x["control_id"] == "A.8.2") # Privileged access rights
    assert c["title"] == "Privileged access rights"
    assert "MFA" in c["evidence"] or "PAM" in c["evidence"]
    assert len(c["compensating_controls"]) > 10
