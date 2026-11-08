"""Canonical multi-standard reference catalog and master discovery survey.

Design contract
---------------
``req_id`` (``R001``..``R133``) is the single shared foreign key used by
every reference workbook, the survey workbook and the SQLite assessment
tables. Nothing else is joined across layers.

Range allocation (deterministic, contiguous, gap-free):

===========  =====================  =====
Range        Standard               Count
===========  =====================  =====
R001-R015    BSI 200-2              15
R016-R025    BSI 200-4              10
R026-R040    ISO/IEC 27001:2022     15
R041-R133    ISO/IEC 27002:2022     93
===========  =====================  =====

The Python catalog is the canonical seed. The workbooks on disk are a real,
round-trippable representation of it (``load_requirement_catalog`` and
``load_survey_catalog`` read them back), which is what makes the Excel files
auditable artefacts rather than decorative exports.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.seed.iso_27002_catalog import ISO_27002_CONTROLS

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

STANDARD_CODES: Tuple[str, ...] = (
    "BSI_200_2",
    "BSI_200_4",
    "ISO_27001",
    "ISO_27002",
)

ENTITY_SCOPES: Tuple[str, ...] = (
    "ORGANIZATION",
    "PROCESS",
    "SUBPROCESS",
    "ACTIVITY",
    "SYSTEM",
    "DATA_ASSET",
    "ROLE",
)

PHASE_CATEGORIES: Tuple[str, ...] = (
    "1_ARCHITECTURE",
    "2_FLOW_AND_LOOPS",
    "3_DATA_AND_SYSTEMS",
    "4_CIA_PROTECTION",
    "5_BIA_CONTINUITY",
    "6_SECURITY_CONTROLS",
)

CIA_OPTIONS: Tuple[str, ...] = ("Normal", "High", "Very High")
BOOL_OPTIONS: Tuple[str, ...] = ("YES", "NO", "PARTIAL")

#: Deterministic validation rules understood by ``mapping_verifier``.
VALIDATION_RULES: Tuple[str, ...] = (
    "NON_EMPTY",
    "ENUM_CIA",
    "RTO_LT_MTPD",
    "CIA_INHERITANCE_MAX",
    "MUST_LINK_PROCESS",
    "ROLE_ASSIGNED",
    "POSITIVE_NUMBER",
    "EVIDENCE_REQUIRED",
)

REQUIREMENT_COLUMNS: Tuple[Tuple[str, int], ...] = (
    ("req_id", 10),
    ("standard_code", 16),
    ("clause_ref", 42),
    ("title", 34),
    ("target_scope", 16),
    ("phase_category", 22),
    ("formal_requirement", 70),
    ("plain_english_requirement", 70),
    ("question_text", 60),
    ("validation_rule", 20),
    ("evidence_needed", 50),
    ("common_gaps", 60),
    ("compensating_controls_template", 55),
    ("concept_type", 14),
    ("allowed_options", 24),
)

SURVEY_COLUMNS: Tuple[Tuple[str, int], ...] = (
    ("question_id", 12),
    ("req_id", 10),
    ("sequence_order", 15),
    ("phase_category", 22),
    ("target_scope", 16),
    ("question_text", 70),
    ("allowed_options", 24),
    ("validation_rule", 20),
    ("concept_type", 14),
    ("standard_code", 16),
    ("clause_ref", 42),
    ("plain_english_requirement", 70),
)

WORKBOOK_SPECS: Tuple[Dict[str, Any], ...] = (
    {
        "filename": "BSI_200_2_Grundschutz_Reference.xlsx",
        "standard_code": "BSI_200_2",
        "title": "BSI IT-Grundschutz — Standard 200-2 (Methodology)",
        "note": (
            "Structural analysis, protection-need assessment and protection "
            "inheritance for business processes, applications, IT systems "
            "and information."
        ),
    },
    {
        "filename": "BSI_200_4_BCM_BIA_Reference.xlsx",
        "standard_code": "BSI_200_4",
        "title": "BSI Standard 200-4 (Business Continuity Management)",
        "note": (
            "Business impact analysis: MTPD, RTO, RPO, minimum continuity "
            "objective, emergency operation and resource dependencies."
        ),
    },
    {
        "filename": "ISO_27001_2022_Reference.xlsx",
        "standard_code": "ISO_27001",
        "title": "ISO/IEC 27001:2022 (ISMS requirements, clauses 4-10)",
        "note": "Normative management-system requirements, including risk assessment and treatment.",
    },
    {
        "filename": "ISO_27002_2022_Reference.xlsx",
        "standard_code": "ISO_27002",
        "title": "ISO/IEC 27002:2022 (93 Annex A control implementations)",
        "note": "All 93 controls across organisational, people, physical and technological chapters.",
    },
)

SURVEY_FILENAME = "Master_Discovery_Survey.xlsx"


# ---------------------------------------------------------------------------
# Requirement construction helper
# ---------------------------------------------------------------------------

def _req(
    req_id: str,
    standard_code: str,
    clause_ref: str,
    title: str,
    target_scope: str,
    formal: str,
    plain: str,
    question: str,
    rule: str,
    evidence: str,
    gaps: str,
    comp: str,
    phase_category: str,
    concept_type: str | None = None,
    allowed_options: Sequence[str] = (),
) -> Dict[str, Any]:
    if target_scope not in ENTITY_SCOPES:
        raise ValueError(f"{req_id}: unknown target_scope {target_scope!r}")
    if rule not in VALIDATION_RULES:
        raise ValueError(f"{req_id}: unknown validation_rule {rule!r}")
    if phase_category not in PHASE_CATEGORIES:
        raise ValueError(f"{req_id}: unknown phase_category {phase_category!r}")
    return {
        "req_id": req_id,
        "standard_code": standard_code,
        "clause_ref": clause_ref,
        "title": title,
        "target_scope": target_scope,
        "phase_category": phase_category,
        "formal_requirement": formal,
        "plain_english_requirement": plain,
        "question_text": question,
        "validation_rule": rule,
        "evidence_needed": evidence,
        "common_gaps": gaps,
        "compensating_controls_template": comp,
        "concept_type": concept_type or "",
        "allowed_options": list(allowed_options),
    }


# ---------------------------------------------------------------------------
# BSI 200-2 — R001..R015
# ---------------------------------------------------------------------------

def _bsi_200_2_requirements() -> List[Dict[str, Any]]:
    return [
        _req(
            "R001", "BSI_200_2", "BSI 200-2 Kap. 4.2 (Strukturanalyse — Geschäftsprozesse)",
            "Business process inventory",
            "ORGANIZATION",
            "Alle Geschäftsprozesse der Institution sind im Rahmen der Strukturanalyse vollständig zu erheben, "
            "eindeutig zu benennen und voneinander abzugrenzen. Anwendungen und IT-Systeme dürfen nicht als "
            "Geschäftsprozesse geführt werden.",
            "List every real business process the organization runs. A process is a sequence of work triggered "
            "by an event that produces a result for a citizen, a customer or another department. Software "
            "products are not processes and belong in the application inventory instead.",
            "Which business processes does the organization actually run, and where is the process register kept?",
            "NON_EMPTY", "Process register, process landscape, owner sign-off per process.",
            "Spreadsheets list software products (HR system, voucher system, invoicing system) under the heading "
            "'business processes', and every row is blanket-labelled 'critical'.",
            "Start a one-page process landscape grouped by department and move every software entry into the "
            "application inventory.",
            "1_ARCHITECTURE", "PROCESS",
        ),
        _req(
            "R002", "BSI_200_2", "BSI 200-2 Kap. 4.2 (Strukturanalyse — Anwendungen und IT-Systeme)",
            "Application and IT system inventory",
            "SYSTEM",
            "Jede Anwendung und jedes IT-System ist zu erfassen und mindestens einem unterstützten "
            "Geschäftsprozess zuzuordnen.",
            "Record every application and IT system and name the business process it supports. A system that "
            "supports no process has no justification and inherits no protection requirement.",
            "Which business process does this system support, and who confirmed that link?",
            "MUST_LINK_PROCESS", "Application inventory with process mapping, CMDB export, licence list.",
            "Systems are listed but never linked to a process, so protection requirements cannot be inherited "
            "and the system cannot be scoped for an audit.",
            "Add one mandatory 'supported process' column to the existing inventory and fill it department by "
            "department with the process owners.",
            "1_ARCHITECTURE", "SYSTEM",
        ),
        _req(
            "R003", "BSI_200_2", "BSI 200-2 Kap. 4.2 (Strukturanalyse — Zuständigkeiten)",
            "Process and asset ownership",
            "ORGANIZATION",
            "Für jeden Geschäftsprozess und jede Anwendung ist eine verantwortliche Organisationseinheit oder "
            "Rolle zu benennen.",
            "Every process, sub-process, activity and system needs a named owner role that answers for it.",
            "Who owns this process or system, and is that assignment written down?",
            "ROLE_ASSIGNED", "RACI matrix, appointment letters, organisation chart with named owners.",
            "Ownership exists informally ('the IT person') but is never documented, so nobody is accountable "
            "during an incident.",
            "Publish a temporary owner list signed by the department heads until formal appointments are issued.",
            "1_ARCHITECTURE", "ROLE",
        ),
        _req(
            "R004", "BSI_200_2", "BSI 200-2 Kap. 4.2 (Strukturanalyse — Informationen)",
            "Information and data asset inventory",
            "DATA_ASSET",
            "Informationen und Daten, die in Geschäftsprozessen verarbeitet werden, sind zu erheben und den "
            "Prozessen zuzuordnen.",
            "List the data each process reads and produces, where it is stored, and how long it is kept.",
            "What data does this process or system read and produce, and where is each data set stored?",
            "NON_EMPTY", "Record of processing activities, data flow description, retention schedule.",
            "Personal data is copied to local drives, mailboxes and USB sticks that appear in no inventory.",
            "Run a one-off mailbox and file-share scan, register the 20 largest unregistered data sets, then "
            "schedule quarterly repeats.",
            "3_DATA_AND_SYSTEMS", "DATA_ASSET",
        ),
        _req(
            "R005", "BSI_200_2", "BSI 200-2 Kap. 4.3 (Schutzbedarfsfeststellung — Vertraulichkeit)",
            "Protection need — confidentiality",
            "ORGANIZATION",
            "Das Schutzbedarfsniveau für Vertraulichkeit (Vertraulichkeit) ist je Geschäftsprozess, Anwendung "
            "und Information mit Begründung festzulegen (Normal, Hoch, Sehr hoch).",
            "State how damaging unauthorised disclosure would be, with a reason. 'Critical' on its own is not a "
            "protection need.",
            "What is the confidentiality protection need for this entity (Normal, High, Very High) and why?",
            "ENUM_CIA", "Completed Schutzbedarfsfeststellung with written justification per entity.",
            "A single blanket label 'kritisch' is applied to every row without separating confidentiality, "
            "integrity and availability.",
            "Rate confidentiality only, using the impact of disclosure, and record the reason in one sentence "
            "per entity.",
            "4_CIA_PROTECTION", None, CIA_OPTIONS,
        ),
        _req(
            "R006", "BSI_200_2", "BSI 200-2 Kap. 4.3 (Schutzbedarfsfeststellung — Integrität)",
            "Protection need — integrity",
            "ORGANIZATION",
            "Das Schutzbedarfsniveau für Integrität ist je Geschäftsprozess, Anwendung und Information mit "
            "Begründung festzulegen (Normal, Hoch, Sehr hoch).",
            "State how damaging incorrect or manipulated data would be, with a reason.",
            "What is the integrity protection need for this entity (Normal, High, Very High) and why?",
            "ENUM_CIA", "Completed Schutzbedarfsfeststellung with written justification per entity.",
            "Integrity is never assessed separately, so manipulation of vouchers or payment data is not "
            "recognised as a risk.",
            "Assess integrity by asking what a single falsified record would cost, and record the answer.",
            "4_CIA_PROTECTION", None, CIA_OPTIONS,
        ),
        _req(
            "R007", "BSI_200_2", "BSI 200-2 Kap. 4.3 (Schutzbedarfsfeststellung — Verfügbarkeit)",
            "Protection need — availability",
            "ORGANIZATION",
            "Das Schutzbedarfsniveau für Verfügbarkeit ist je Geschäftsprozess, Anwendung und Information mit "
            "Begründung festzulegen (Normal, Hoch, Sehr hoch).",
            "State how damaging downtime would be, with a reason and a tolerable outage length.",
            "What is the availability protection need for this entity (Normal, High, Very High) and why?",
            "ENUM_CIA", "Completed Schutzbedarfsfeststellung with written justification per entity.",
            "Availability is assumed to be 'normal' for every system until an outage happens, because no "
            "tolerable downtime was ever written down.",
            "Derive availability from the business impact analysis instead of guessing, and link the two records.",
            "4_CIA_PROTECTION", None, CIA_OPTIONS,
        ),
        _req(
            "R008", "BSI_200_2", "BSI 200-2 Kap. 4.3 (Maximumprinzip / Vererbung)",
            "Protection inheritance — maximum principle",
            "SYSTEM",
            "Erfüllt eine Anwendung oder ein IT-System mehrere Geschäftsprozesse mit unterschiedlichem "
            "Schutzbedarf, so ist für das System das Maximum der Schutzbedarfe maßgeblich (Maximumprinzip).",
            "A system inherits the highest protection need of all the processes it supports. It can never be "
            "protected less than its most demanding process.",
            "Does this system carry at least the highest confidentiality, integrity and availability need of "
            "every process that depends on it?",
            "CIA_INHERITANCE_MAX", "Inheritance calculation across all dependent processes, signed by the owner.",
            "Systems are rated independently of the processes they serve, so a critical process rests on a "
            "system classified as 'normal'.",
            "Recompute every system rating from its dependent processes and correct the register; do not rate "
            "systems from memory.",
            "4_CIA_PROTECTION", None,
        ),
        _req(
            "R009", "BSI_200_2", "BSI 200-2 Kap. 4.3 (Kumulationseffekt)",
            "Protection inheritance — accumulation effect",
            "SYSTEM",
            "Übersteigt die Kumulation der Schutzbedarfe mehrerer auf einem System verarbeiteter Prozesse den "
            "Einzelschutzbedarf, ist dies bei der Schutzbedarfsfeststellung zu berücksichtigen.",
            "Even when each individual process is only 'normal', many processes on one shared system can add up "
            "to a higher overall protection need.",
            "How many processes share this system, and does the combined load raise its overall protection need?",
            "CIA_INHERITANCE_MAX", "Justification documenting the accumulation assessment.",
            "One shared file server or database carries a dozen processes, each rated normal, and is itself "
            "rated normal.",
            "Document the accumulation reasoning per shared system and raise the system rating where the "
            "combined dependency justifies it.",
            "4_CIA_PROTECTION", None,
        ),
        _req(
            "R010", "BSI_200_2", "BSI 200-2 Kap. 4.3 (Verteilungseffekt)",
            "Protection inheritance — distribution effect",
            "SYSTEM",
            "Ein gegenüber den Geschäftsprozessen höherer Schutzbedarf eines Systems ist nur dann zulässig, "
            "wenn er begründet und durch geeignete Maßnahmen (z. B. Trennung) nachvollziehbar ist "
            "(Verteilungseffekt).",
            "A system may be rated higher than the processes it serves only with a written justification and "
            "separation measures.",
            "If this system is rated above the processes it serves, what justifies that rating and how is it "
            "enforced?",
            "CIA_INHERITANCE_MAX", "Written justification plus evidence of the separating measure.",
            "A system inherits an inflated rating nobody can explain, which wastes budget and hides the real "
            "risk.",
            "Re-derive the rating from the dependent processes; keep any uplift only with a named justification "
            "and a separation measure.",
            "4_CIA_PROTECTION", None,
        ),
        _req(
            "R011", "BSI_200_2", "BSI 200-2 Kap. 4.4 (Modellierung) / Kompendium ISMS.1",
            "Systematic security concept (IT-Grundschutz modelling)",
            "ORGANIZATION",
            "Auf Basis der Strukturanalyse und Schutzbedarfsfeststellung ist ein Sicherheitskonzept nach "
            "IT-Grundschutz zu modellieren (Basis-, Standard- oder Kern-Absicherung) und fortzuschreiben.",
            "Turn the inventory and protection ratings into selected safeguards with an owner and a review "
            "date, instead of a one-off document.",
            "Which safeguards are selected for this entity, who owns each one, and when was the concept last "
            "reviewed?",
            "EVIDENCE_REQUIRED", "IT-Grundschutz profile, selected Bausteine, safeguard list with owners and review dates.",
            "A security concept was produced once for an audit and never updated after systems changed.",
            "Freeze the current inventory as the baseline, select safeguards for the top five risks only, and "
            "put a quarterly review date in the calendar.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R012", "BSI_200_2", "BSI Kompendium ORP.4 (Identitäts- und Berechtigungsmanagement)",
            "Identity and access management",
            "SYSTEM",
            "Identitäten sind eindeutig zu verwalten; Berechtigungen sind nach dem Erforderlichkeitsprinzip "
            "zu vergeben, regelmäßig zu überprüfen und bei Austritt unverzüglich zu entziehen.",
            "Every user has a personal account, permissions follow the job, they are reviewed on a schedule and "
            "removed on the day someone leaves.",
            "How are accounts created, reviewed and removed for this system, and who approves access?",
            "EVIDENCE_REQUIRED", "Account list, approval tickets, quarterly access review minutes, leaver checklist.",
            "Shared generic accounts are used by several staff, and permissions from previous roles are never "
            "removed.",
            "Enforce personal accounts for the named system, add an HR-triggered leaver ticket, and review the "
            "existing group memberships once.",
            "6_SECURITY_CONTROLS", "ROLE",
        ),
        _req(
            "R013", "BSI_200_2", "BSI Kompendium OPS.1.1.3 (Patch- und Änderungsmanagement)",
            "Patch and change management",
            "SYSTEM",
            "Änderungen an IT-Systemen und Anwendungen sind zu planen, zu testen, zu genehmigen und "
            "nachvollziehbar zu dokumentieren; Sicherheitsupdates sind zeitnah einzuspielen.",
            "Changes and security patches are planned, tested, approved and recorded, and urgent security "
            "patches have a defined deadline.",
            "How is a change or security patch approved and tested before it reaches production, and where is "
            "that recorded?",
            "EVIDENCE_REQUIRED", "Change tickets with approval, test evidence, patch status report.",
            "Updates are applied ad hoc by whoever is available, without a test step or a record.",
            "Introduce a single change ticket template with an approval field and a patch deadline, and record "
            "the last five changes retroactively.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R014", "BSI_200_2", "BSI Kompendium OPS.1.1.5 / DER.1 / DER.2.1",
            "Logging, detection and incident handling",
            "SYSTEM",
            "Sicherheitsrelevante Ereignisse sind zu protokollieren, auszuwerten und in einem definierten "
            "Melde- und Incident-Prozess zu behandeln.",
            "Security-relevant events are logged, someone actually reads the logs, and there is a defined route "
            "for reporting and handling an incident.",
            "Which events are logged, who reviews them and how often, and how is an incident reported?",
            "EVIDENCE_REQUIRED", "Logging concept, log retention setting, incident register, on-call contact list.",
            "Logs exist but nobody reviews them, and incident handling depends on who notices first.",
            "Define one alert path and one named on-call contact per system, and start a minimal incident "
            "register that records date, impact and action.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R015", "BSI_200_2", "BSI Kompendium CON.3 / CON.6 (Datensicherung und Löschen)",
            "Backup and deletion concept",
            "SYSTEM",
            "Für Daten ist ein Datensicherungskonzept (Umfang, Zyklus, Aufbewahrung, Wiederherstellungstest) "
            "und ein geregeltes Löschkonzept zu erstellen und umzusetzen.",
            "Backups run on a defined cycle, restores are tested, and data is deleted when its retention period "
            "ends.",
            "What is backed up, how often, where is the copy kept, and when was a restore last tested?",
            "EVIDENCE_REQUIRED", "Backup schedule, restore test record with date and scope, deletion log.",
            "Backups exist but have never been restored, and copies sit on the same server they protect.",
            "Run one documented restore test this quarter, record the result, and move one copy off the primary "
            "system.",
            "6_SECURITY_CONTROLS", None,
        ),
    ]


# ---------------------------------------------------------------------------
# BSI 200-4 — R016..R025
# ---------------------------------------------------------------------------

def _bsi_200_4_requirements() -> List[Dict[str, Any]]:
    return [
        _req(
            "R016", "BSI_200_4", "BSI 200-4 Kap. 3.2 (Business Impact Analyse — Scope)",
            "Business impact analysis scope",
            "PROCESS",
            "Die für die Aufrechterhaltung der Leistungserbringung kritischen Geschäftsprozesse sind zu "
            "identifizieren und die BIA ist für diese Prozesse durchzuführen.",
            "Decide which processes are time-critical, and run the impact analysis on those instead of on "
            "everything.",
            "Which processes are time-critical, and what was the basis for that decision?",
            "NON_EMPTY", "BIA scope decision, list of time-critical processes with justification.",
            "Every process is labelled critical, so the genuinely time-critical ones cannot be prioritised "
            "during an outage.",
            "Rank processes by damage per hour of outage and keep only the top group in the BIA scope.",
            "5_BIA_CONTINUITY", "PROCESS",
        ),
        _req(
            "R017", "BSI_200_4", "BSI 200-4 Kap. 3.2 (Business Impact Analyse — MTPD)",
            "Maximum tolerable period of disruption (MTPD)",
            "PROCESS",
            "Für jeden zeitkritischen Geschäftsprozess ist die maximal tolerierbare Ausfallzeit (MTPD) zu "
            "ermitteln, ab der die Folgen nicht mehr tragbar sind.",
            "Write down the number of hours or days after which the damage from an outage becomes "
            "unacceptable.",
            "After how many hours or days of outage does this process cause unacceptable damage, and why?",
            "POSITIVE_NUMBER", "MTPD value per time-critical process with the damage reasoning.",
            "No tolerable downtime is recorded anywhere, so recovery priorities are decided by whoever shouts "
            "loudest during an incident.",
            "Ask the process owner for the first hour at which legal, financial or safety consequences become "
            "unacceptable, and record that number.",
            "5_BIA_CONTINUITY", None,
        ),
        _req(
            "R018", "BSI_200_4", "BSI 200-4 Kap. 3.2 (Business Impact Analyse — RTO)",
            "Recovery time objective (RTO)",
            "PROCESS",
            "Die Wiederanlaufzeit (RTO) ist je zeitkritischem Geschäftsprozess so festzulegen, dass sie "
            "zwingend kleiner als die maximal tolerierbare Ausfallzeit (MTPD) ist (RTO < MTPD).",
            "State how long recovery is allowed to take. Recovery must be faster than the point where damage "
            "becomes unacceptable.",
            "How many hours may recovery take (RTO), and is that strictly less than the tolerable outage (MTPD)?",
            "RTO_LT_MTPD", "Documented RTO per time-critical process, compared against the MTPD.",
            "An RTO is quoted that equals or exceeds the MTPD, which means the plan accepts damage the "
            "business says it cannot survive.",
            "Reduce RTO below MTPD with a partial restore or an emergency manual procedure, and record the gap "
            "until it is closed.",
            "5_BIA_CONTINUITY", None,
        ),
        _req(
            "R019", "BSI_200_4", "BSI 200-4 Kap. 3.2 (Business Impact Analyse — Datenverlust)",
            "Recovery point objective (RPO)",
            "PROCESS",
            "Der maximal zulässige Datenverlust ist je zeitkritischem Geschäftsprozess zu bestimmen und mit "
            "dem Sicherungszyklus abzugleichen.",
            "State how much data may be lost, then check that the backup cycle is at least that frequent.",
            "How many hours of data may be lost at most, and does the backup cycle match that figure?",
            "POSITIVE_NUMBER", "RPO per process, backup cycle setting, comparison note.",
            "A backup runs nightly while the process records transactions continuously, so the real loss window "
            "is far larger than assumed.",
            "Set the RPO explicitly and align the backup cycle to it, or record the accepted residual loss.",
            "5_BIA_CONTINUITY", None,
        ),
        _req(
            "R020", "BSI_200_4", "BSI 200-4 Kap. 3.2 (Mindestziel / MBCO)",
            "Minimum business continuity objective (MBCO)",
            "PROCESS",
            "Für zeitkritische Prozesse ist festzulegen, welche Mindestleistung im Notfall noch erbracht werden "
            "muss (Minimum Business Continuity Objective).",
            "Decide what minimum service level must still be delivered during an emergency, so recovery has a "
            "defined target.",
            "What minimum output must still be delivered during an emergency, and at what volume?",
            "NON_EMPTY", "MBCO statement per time-critical process, agreed with the owner.",
            "The plan aims for full recovery, which is unrealistic, and no reduced but acceptable service level "
            "is defined.",
            "Define a reduced emergency service level (for example manual issuance of the 20 most urgent cases "
            "per day) and agree it with the owner.",
            "5_BIA_CONTINUITY", None,
        ),
        _req(
            "R021", "BSI_200_4", "BSI 200-4 Kap. 3.5 (Notbetrieb / BC-Pläne)",
            "Emergency operation procedure",
            "PROCESS",
            "Für den Notbetrieb sind dokumentierte Verfahren, Verantwortlichkeiten und Hilfsmittel "
            "bereitzustellen, damit die Mindestleistung ohne die Primärsysteme erbracht werden kann.",
            "Describe the manual fallback: who does what, on paper or in a stand-in system, when the primary "
            "system is unavailable.",
            "What exactly do staff do while the primary system is down, and where is that procedure written down?",
            "EVIDENCE_REQUIRED", "Emergency procedure document, paper forms, offline contact list, training record.",
            "Staff improvise during outages; the fallback procedure exists only in individual memory, so "
            "quality depends on who is on shift.",
            "Write a one-page emergency procedure per critical process, keep printed forms on site, and walk it "
            "through once with the team.",
            "5_BIA_CONTINUITY", None,
        ),
        _req(
            "R022", "BSI_200_4", "BSI 200-4 Kap. 3.2 (Ressourcen-BIA — IT-Systeme)",
            "Resource BIA — applications and IT systems",
            "PROCESS",
            "Für zeitkritische Prozesse ist zu ermitteln, welche Anwendungen und IT-Systeme für die "
            "Mindestleistung im Notfall erforderlich sind.",
            "Name the systems the process truly needs for emergency operation, and mark the ones that can stay "
            "down.",
            "Which systems must be available for this process to meet its emergency service level?",
            "MUST_LINK_PROCESS", "Resource dependency list per time-critical process, recovery sequence.",
            "Every system is treated as equally critical, so recovery order is never defined and the most "
            "needed system may recover last.",
            "Mark only genuinely required systems per process, then derive a recovery sequence from those marks.",
            "5_BIA_CONTINUITY", "SYSTEM",
        ),
        _req(
            "R023", "BSI_200_4", "BSI 200-4 Kap. 3.2 (Ressourcen-BIA — Personal)",
            "Resource BIA — personnel and roles",
            "PROCESS",
            "Für zeitkritische Prozesse ist zu ermitteln, welche Rollen und wie viele Personen für den "
            "Notbetrieb erforderlich sind, einschließlich Vertretungsregelungen.",
            "Name the roles needed in an emergency, how many people that means, and who deputises for whom.",
            "Which roles and how many people are needed for emergency operation, and who deputises?",
            "ROLE_ASSIGNED", "Emergency staffing list, deputy matrix, availability and reachability record.",
            "Emergency operation depends on one specific person, and no deputy is named.",
            "Name a deputy for every role needed in an emergency and record reachability outside office hours.",
            "5_BIA_CONTINUITY", "ROLE",
        ),
        _req(
            "R024", "BSI_200_4", "BSI 200-4 Kap. 3.2 (Ressourcen-BIA — Informationen)",
            "Resource BIA — data and documents",
            "PROCESS",
            "Für zeitkritische Prozesse ist zu ermitteln, welche Informationen und Unterlagen im Notbetrieb "
            "verfügbar sein müssen und in welcher Form.",
            "Name the data and documents that must be reachable during an emergency, and in which form "
            "(system, export, paper).",
            "Which data and documents must be available during an emergency, and in what form?",
            "NON_EMPTY", "Emergency data list, offline export, printed registers, storage location.",
            "The emergency procedure assumes the document management system is reachable, which defeats the "
            "purpose of an emergency procedure.",
            "Prepare an offline export of the minimum data set and store it where staff can reach it without "
            "the primary system.",
            "5_BIA_CONTINUITY", "DATA_ASSET",
        ),
        _req(
            "R025", "BSI_200_4", "BSI 200-4 Kap. 3.6 (Übungen und Aufrechterhaltung) / Anbietermanagement",
            "Suppliers, plan testing and maintenance",
            "ORGANIZATION",
            "Externe Dienstleister und Lieferanten sind in die Notfallplanung einzubeziehen; BC-Pläne sind "
            "regelmäßig zu üben, zu bewerten und fortzuschreiben.",
            "External providers are part of the continuity plan, and the plan is exercised and updated on a "
            "schedule rather than filed away.",
            "Which suppliers are needed during an emergency, how are they reached, and when was the continuity "
            "plan last exercised?",
            "EVIDENCE_REQUIRED", "Supplier emergency contacts, contract clauses on continuity, exercise report with date.",
            "Supplier dependencies are undocumented and the continuity plan has never been exercised.",
            "Collect emergency contacts for the three most important suppliers and run one tabletop exercise, "
            "then record the findings.",
            "5_BIA_CONTINUITY", None,
        ),
    ]


# ---------------------------------------------------------------------------
# ISO 27001 — R026..R040
# ---------------------------------------------------------------------------

def _iso_27001_requirements() -> List[Dict[str, Any]]:
    return [
        _req(
            "R026", "ISO_27001", "ISO 27001:2022 Kl. 4.1",
            "Understanding the organization and its context",
            "ORGANIZATION",
            "The organization shall determine external and internal issues relevant to its purpose and that "
            "affect its ability to achieve the intended outcome(s) of its information security management system.",
            "Know which outside and inside factors can affect information security, and keep that view current.",
            "Which external and internal factors affect information security for this organization, and where is that recorded?",
            "EVIDENCE_REQUIRED", "Context analysis document, PESTEL or SWOT style assessment, management review minutes.",
            "Context analysis was written once for certification and never revisited after a merger, new "
            "regulation or outsourcing decision.",
            "Refresh the context analysis with the department heads once a year and record the changes only.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R027", "ISO_27001", "ISO 27001:2022 Kl. 4.2",
            "Needs and expectations of interested parties",
            "ORGANIZATION",
            "The organization shall determine the interested parties relevant to the ISMS, their relevant "
            "requirements, and which of those will be addressed.",
            "Name who has demands on information security (citizens, auditors, regulators, suppliers) and which "
            "demands are binding.",
            "Which interested parties impose information security requirements, and which of them are binding?",
            "EVIDENCE_REQUIRED", "Interested-party register, legal and contractual requirement list.",
            "Regulatory and contractual obligations are tracked informally, so new duties are missed until an "
            "audit or an incident.",
            "Create one requirement register covering legal, contractual and internal obligations with a review "
            "date.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R028", "ISO_27001", "ISO 27001:2022 Kl. 4.3",
            "ISMS scope",
            "ORGANIZATION",
            "The organization shall determine the boundaries and applicability of the ISMS to establish its scope, "
            "considering external and internal issues, interested party requirements and interfaces with third "
            "parties.",
            "Define what the management system covers and what it explicitly excludes, including interfaces to "
            "external providers.",
            "Which organizational units, locations, processes and systems are inside the ISMS scope, and what is excluded?",
            "NON_EMPTY", "Scope statement, scope diagram, list of excluded units with justification.",
            "The scope statement is a vague sentence, so nobody can tell whether a given system is audited or not.",
            "Write the scope as an explicit list of units, locations and systems, and name every exclusion with "
            "its justification.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R029", "ISO_27001", "ISO 27001:2022 Kl. 4.4",
            "ISMS and its processes",
            "ORGANIZATION",
            "The organization shall establish, implement, maintain and continually improve the ISMS, including "
            "the processes needed and their interactions.",
            "The management system itself has processes, responsibilities and interactions that are defined and "
            "maintained.",
            "Which processes make up the management system, who owns each, and how do they interact?",
            "EVIDENCE_REQUIRED", "ISMS process map, role descriptions, process descriptions with interfaces.",
            "The management system exists on paper as a folder of documents with no defined processes or owners.",
            "Define the five core ISMS processes (risk, awareness, incident, supplier, audit) with an owner each.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R030", "ISO_27001", "ISO 27001:2022 Kl. 5.1",
            "Leadership and commitment",
            "ORGANIZATION",
            "Top management shall demonstrate leadership and commitment with respect to the ISMS by taking "
            "accountability, ensuring policy and objectives are established and integrated into business "
            "processes, and providing resources.",
            "Management visibly owns information security: it approves the policy, provides resources and is "
            "accountable, not just informed.",
            "How does top management demonstrate accountability for information security, and what is minuted?",
            "EVIDENCE_REQUIRED", "Management review minutes, signed policy, resourcing decisions, escalation records.",
            "Security is delegated entirely to IT and appears in no management agenda.",
            "Put one standing information security item on the management agenda and minute decisions taken.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R031", "ISO_27001", "ISO 27001:2022 Kl. 5.2",
            "Information security policy",
            "ORGANIZATION",
            "Top management shall establish an information security policy that includes a commitment to satisfy "
            "applicable requirements and to continual improvement, and that is communicated within the organization.",
            "A current, approved policy exists and staff can actually find and understand it.",
            "Where is the current information security policy published, who approved it, and how is it communicated?",
            "EVIDENCE_REQUIRED", "Approved policy document, publication evidence, acknowledgement log.",
            "A policy from several years ago sits on a share nobody opens, and no one can confirm acknowledgement.",
            "Publish a short current policy, send it with acknowledgement tracking, and schedule an annual review.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R032", "ISO_27001", "ISO 27001:2022 Kl. 5.3",
            "Roles, responsibilities and authorities",
            "ORGANIZATION",
            "Top management shall ensure that responsibilities and authorities for relevant roles are assigned "
            "and communicated within the organization.",
            "Every security duty has a named person or role with the authority to act, and that is communicated.",
            "Which roles carry information security responsibility and authority, and how is that communicated?",
            "ROLE_ASSIGNED", "Role descriptions, appointment letters, RACI for security duties.",
            "Security duties are spread informally across IT staff with no mandate to enforce anything.",
            "Publish a short responsibility matrix for the ten most important security duties and name an owner "
            "for each.",
            "6_SECURITY_CONTROLS", "ROLE",
        ),
        _req(
            "R033", "ISO_27001", "ISO 27001:2022 Kl. 6.1.2",
            "Information security risk assessment",
            "ORGANIZATION",
            "The organization shall define and apply an information security risk assessment process that "
            "establishes risk criteria, ensures repeatable results, identifies risks and analyses and evaluates them.",
            "Risks are assessed with a stated method and criteria, so two assessments of the same thing produce "
            "the same result.",
            "What method and criteria are used to assess information security risk, and where are the results recorded?",
            "EVIDENCE_REQUIRED", "Risk assessment method description, risk register with scores and owners.",
            "Risk is judged by gut feeling, and the same system is rated differently by different people.",
            "Adopt one simple likelihood-times-impact grid, document it in one page, and re-score the top risks "
            "with it.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R034", "ISO_27001", "ISO 27001:2022 Kl. 6.1.3",
            "Risk treatment and Statement of Applicability",
            "ORGANIZATION",
            "The organization shall define and apply a risk treatment process, select treatment options and "
            "produce a Statement of Applicability that records the necessity of each Annex A control and its "
            "inclusion or exclusion justification.",
            "For every Annex A control, there is a recorded decision: included with justification, or excluded "
            "with justification.",
            "Where is the Statement of Applicability, and does every control have an inclusion or exclusion justification?",
            "EVIDENCE_REQUIRED", "Statement of Applicability covering all 93 controls, risk treatment plan.",
            "The Statement of Applicability simply declares all controls applicable, which suggests it was "
            "copied rather than derived.",
            "Derive exclusions from real risk decisions for the ten controls where exclusion is plausible, and "
            "justify each one.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R035", "ISO_27001", "ISO 27001:2022 Kl. 6.2",
            "Information security objectives and planning",
            "ORGANIZATION",
            "The organization shall establish information security objectives at relevant functions and levels, "
            "which are measurable, monitored, communicated and updated as appropriate.",
            "Set a small number of measurable security goals with a target date and a named owner.",
            "Which measurable information security objectives exist, who owns them and what is the current status?",
            "NON_EMPTY", "Objective list with metric, target, owner and review cadence.",
            "Objectives are phrases such as 'improve security', which cannot be measured or reviewed.",
            "Replace them with three measurable objectives (for example restore test coverage, overdue patches, "
            "completed access reviews) and report them quarterly.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R036", "ISO_27001", "ISO 27001:2022 Kl. 7.1-7.4",
            "Resources, competence, awareness and communication",
            "ORGANIZATION",
            "The organization shall determine and provide the resources needed for the ISMS, ensure persons are "
            "competent, make them aware of the policy and their contribution, and determine internal and external "
            "communications relevant to the ISMS.",
            "People have the time, skills and information to do their security duties, and security messages "
            "reach the right audiences.",
            "How are security resources, competences, awareness and communication managed for this organization?",
            "EVIDENCE_REQUIRED", "Resource plan, training records, awareness campaign evidence, communication plan.",
            "Awareness consists of one email per year, and no training record exists.",
            "Run one short role-specific briefing per department per year and keep attendance records.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R037", "ISO_27001", "ISO 27001:2022 Kl. 7.5",
            "Documented information",
            "ORGANIZATION",
            "The ISMS shall include documented information required by the standard and determined by the "
            "organization as necessary, with appropriate identification, format, review and control of versions "
            "and access.",
            "Documents have an owner, a version, an approval and a review date, and obsolete versions are removed.",
            "How is security documentation versioned, approved and retired, and who controls access?",
            "EVIDENCE_REQUIRED", "Document register with versions and owners, access control list, archive of superseded versions.",
            "Multiple contradictory versions of the same procedure circulate, and nobody knows which one applies.",
            "Create a single document register with owner, version and review date, and archive everything else "
            "as superseded.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R038", "ISO_27001", "ISO 27001:2022 Kl. 8.1",
            "Operational planning and control — documented process flow",
            "PROCESS",
            "The organization shall plan, implement and control the processes needed to meet ISMS requirements by "
            "establishing criteria for the processes and implementing control of the processes in accordance with "
            "the criteria.",
            "The process is written down as an ordered flow with decision points and repeats, because a process "
            "that is not described cannot be controlled.",
            "What are the ordered sub-processes and activities of this process, where are the decision branches, "
            "and which steps repeat in a loop?",
            "NON_EMPTY", "Process description or model, decision criteria, rework and escalation paths.",
            "Staff describe the happy path only; rejections, rework and escalation are undocumented and handled "
            "differently by each person.",
            "Describe the flow including the rejection branch and the repeat condition, and model it as a process "
            "diagram with swimlanes per role.",
            "2_FLOW_AND_LOOPS", "PROCESS",
        ),
        _req(
            "R039", "ISO_27001", "ISO 27001:2022 Kl. 9.1-9.3",
            "Monitoring, internal audit and management review",
            "ORGANIZATION",
            "The organization shall evaluate information security performance, conduct internal audits at planned "
            "intervals and review the ISMS at planned intervals to ensure its continuing suitability and effectiveness.",
            "Security performance is measured, audited independently and reviewed by management on a schedule.",
            "What is monitored, when was the last internal audit, and what did the last management review decide?",
            "EVIDENCE_REQUIRED", "Monitoring metrics, internal audit report and plan, management review minutes with actions.",
            "There is no internal audit programme, and the last management review was the certification audit itself.",
            "Define a one-year internal audit plan covering the highest-risk areas and minute one management "
            "review with decisions.",
            "6_SECURITY_CONTROLS", None,
        ),
        _req(
            "R040", "ISO_27001", "ISO 27001:2022 Kl. 10.1-10.2",
            "Nonconformity, corrective action and continual improvement",
            "ORGANIZATION",
            "The organization shall react to nonconformities, evaluate the need for action to eliminate their "
            "causes, implement action, review effectiveness and continually improve the suitability, adequacy "
            "and effectiveness of the ISMS.",
            "Findings lead to a recorded root cause, an action, an owner and a check that the action actually worked.",
            "How are findings and incidents turned into corrective actions that are verified as effective?",
            "EVIDENCE_REQUIRED", "Nonconformity register, root cause analysis, effectiveness review evidence.",
            "Findings are closed by writing 'done' in a spreadsheet without any root cause or effectiveness check.",
            "Add three fields to the existing list (root cause, owner, effectiveness check) and review open items "
            "monthly.",
            "6_SECURITY_CONTROLS", None,
        ),
    ]


# ---------------------------------------------------------------------------
# ISO 27002 — R041..R133
# ---------------------------------------------------------------------------

#: Scope overrides where the chapter default is too coarse.
_ISO_27002_SCOPE_OVERRIDES: Dict[str, str] = {
    "A.5.9": "DATA_ASSET",
    "A.5.12": "DATA_ASSET",
    "A.5.13": "DATA_ASSET",
    "A.5.14": "PROCESS",
    "A.5.15": "SYSTEM",
    "A.5.16": "SYSTEM",
    "A.5.17": "SYSTEM",
    "A.5.18": "SYSTEM",
    "A.5.23": "SYSTEM",
    "A.5.29": "PROCESS",
    "A.5.30": "PROCESS",
    "A.5.33": "DATA_ASSET",
    "A.5.34": "DATA_ASSET",
    "A.8.10": "DATA_ASSET",
    "A.8.11": "DATA_ASSET",
    "A.8.12": "DATA_ASSET",
    "A.8.33": "DATA_ASSET",
}


def _iso_27002_scope(control: Dict[str, Any]) -> str:
    control_id = control["control_id"]
    if control_id in _ISO_27002_SCOPE_OVERRIDES:
        return _ISO_27002_SCOPE_OVERRIDES[control_id]
    chapter = str(control.get("chapter", ""))
    if chapter.startswith("8."):
        return "SYSTEM"
    return "ORGANIZATION"


def _iso_27002_requirements() -> List[Dict[str, Any]]:
    if len(ISO_27002_CONTROLS) != 93:
        raise ValueError(
            f"ISO 27002 catalog must contain exactly 93 controls, found {len(ISO_27002_CONTROLS)}"
        )

    requirements: List[Dict[str, Any]] = []
    for offset, control in enumerate(ISO_27002_CONTROLS):
        req_id = f"R{41 + offset:03d}"
        title = control["title"]
        formal = control["requirement"]
        evidence = control.get("evidence", "")
        gaps = control.get("common_gaps", "")
        comp = control.get("compensating_controls", "")
        plain = f"{formal} Auditable proof: {evidence}" if evidence else formal
        question = (
            f"Is '{title}' actually done today for the affected process or system? "
            "Describe who performs it, how often, and the evidence that proves it."
        )
        requirements.append(
            _req(
                req_id,
                "ISO_27002",
                f"ISO 27002:2022 {control['control_id']}",
                title,
                _iso_27002_scope(control),
                formal,
                plain,
                question,
                "EVIDENCE_REQUIRED",
                evidence,
                gaps,
                comp,
                "6_SECURITY_CONTROLS",
                None,
            )
        )
    return requirements


# ---------------------------------------------------------------------------
# Catalog assembly
# ---------------------------------------------------------------------------

def build_requirement_catalog() -> List[Dict[str, Any]]:
    """Return all 133 requirements in deterministic ``req_id`` order."""
    catalog: List[Dict[str, Any]] = []
    catalog.extend(_bsi_200_2_requirements())
    catalog.extend(_bsi_200_4_requirements())
    catalog.extend(_iso_27001_requirements())
    catalog.extend(_iso_27002_requirements())
    _assert_catalog_integrity(catalog)
    return catalog


def build_survey_catalog(catalog: List[Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    """Return one survey question per requirement, ordered by ``sequence_order``."""
    catalog = catalog if catalog is not None else build_requirement_catalog()
    questions: List[Dict[str, Any]] = []
    for order, req in enumerate(catalog, start=1):
        questions.append(
            {
                "question_id": f"Q_{req['req_id']}",
                "req_id": req["req_id"],
                "sequence_order": order,
                "phase_category": req["phase_category"],
                "target_scope": req["target_scope"],
                "question_text": req["question_text"],
                "allowed_options": list(req["allowed_options"]),
                "validation_rule": req["validation_rule"],
                "concept_type": req["concept_type"],
                "standard_code": req["standard_code"],
                "clause_ref": req["clause_ref"],
                "plain_english_requirement": req["plain_english_requirement"],
            }
        )
    return questions


def _assert_catalog_integrity(catalog: List[Dict[str, Any]]) -> None:
    if len(catalog) != 133:
        raise ValueError(f"Requirement catalog must contain 133 entries, found {len(catalog)}")

    expected_ids = [f"R{index:03d}" for index in range(1, 134)]
    actual_ids = [req["req_id"] for req in catalog]
    if actual_ids != expected_ids:
        missing = sorted(set(expected_ids) - set(actual_ids))
        duplicated = sorted({rid for rid in actual_ids if actual_ids.count(rid) > 1})
        raise ValueError(
            "Requirement ids must be contiguous R001..R133. "
            f"missing={missing} duplicated={duplicated}"
        )

    for req in catalog:
        if not req["formal_requirement"].strip():
            raise ValueError(f"{req['req_id']}: empty formal_requirement")
        if not req["plain_english_requirement"].strip():
            raise ValueError(f"{req['req_id']}: empty plain_english_requirement")
        if not req["question_text"].strip():
            raise ValueError(f"{req['req_id']}: empty question_text")

    code_counts: Dict[str, int] = {}
    for req in catalog:
        code_counts[req["standard_code"]] = code_counts.get(req["standard_code"], 0) + 1
    expected_counts = {"BSI_200_2": 15, "BSI_200_4": 10, "ISO_27001": 15, "ISO_27002": 93}
    if code_counts != expected_counts:
        raise ValueError(f"Standard allocation mismatch: {code_counts} != {expected_counts}")


# ---------------------------------------------------------------------------
# Excel writing
# ---------------------------------------------------------------------------

_HEADER_FILL = PatternFill(start_color="1D4ED8", end_color="1D4ED8", fill_type="solid")
_HEADER_FONT = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
_BODY_FONT = Font(name="Segoe UI", size=10)
_MONO_FONT = Font(name="Consolas", size=10, bold=True)
_BORDER_SIDE = Side(border_style="thin", color="D8D6CE")
_CELL_BORDER = Border(left=_BORDER_SIDE, right=_BORDER_SIDE, top=_BORDER_SIDE, bottom=_BORDER_SIDE)
_WRAP = Alignment(vertical="top", wrap_text=True)


def _write_table_sheet(ws, columns: Sequence[Tuple[str, int]], rows: Iterable[Dict[str, Any]]) -> None:
    for col_index, (name, width) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_index, value=name)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col_index)].width = width
    ws.row_dimensions[1].height = 26

    for row_index, record in enumerate(rows, start=2):
        for col_index, (name, _width) in enumerate(columns, start=1):
            value = record.get(name, "")
            if isinstance(value, (list, tuple)):
                value = "; ".join(str(item) for item in value)
            cell = ws.cell(row=row_index, column=col_index, value=value)
            cell.font = _MONO_FONT if name in ("req_id", "question_id") else _BODY_FONT
            cell.border = _CELL_BORDER
            cell.alignment = _WRAP
    ws.freeze_panes = "A2"


def _write_readme_sheet(ws, spec: Dict[str, Any], requirements: Sequence[Dict[str, Any]]) -> None:
    first, last = requirements[0]["req_id"], requirements[-1]["req_id"]
    lines = [
        (spec["title"], True),
        ("", False),
        (spec["note"], False),
        ("", False),
        (f"Requirement id range: {first} .. {last}  ({len(requirements)} requirements)", False),
        (f"Standard code: {spec['standard_code']}", False),
        ("", False),
        ("How to read this workbook", True),
        ("  req_id                Primary key. The same value is the foreign key in the survey", False),
        ("                        workbook and in every SQLite assessment table. Never renumber.", False),
        ("  clause_ref            Clause or module reference in the source standard.", False),
        ("  target_scope          Entity type this requirement is assessed against", False),
        ("                        (ORGANIZATION, PROCESS, SUBPROCESS, ACTIVITY, SYSTEM, DATA_ASSET, ROLE).", False),
        ("  phase_category        Survey phase used to order the discovery interview.", False),
        ("  validation_rule       Deterministic rule applied by the verifier:", False),
        ("                        NON_EMPTY, ENUM_CIA, RTO_LT_MTPD, CIA_INHERITANCE_MAX,", False),
        ("                        MUST_LINK_PROCESS, ROLE_ASSIGNED, POSITIVE_NUMBER, EVIDENCE_REQUIRED.", False),
        ("  evidence_needed       What an auditor would ask to see.", False),
        ("  common_gaps           Typical real-world failure for this requirement.", False),
        ("  compensating_controls Pragmatic interim measure when full compliance is not yet possible.", False),
        ("", False),
        ("Editing rules", True),
        ("  Text columns may be edited freely. Do not change req_id values, do not delete rows,", False),
        ("  and do not add rows outside the allocated range without also updating the survey.", False),
    ]
    for row_index, (text, is_heading) in enumerate(lines, start=1):
        cell = ws.cell(row=row_index, column=1, value=text)
        cell.font = Font(name="Segoe UI", size=12, bold=True) if is_heading else _BODY_FONT
        cell.alignment = Alignment(vertical="top", wrap_text=False)
    ws.column_dimensions["A"].width = 110


def write_standard_workbook(path: str, spec: Dict[str, Any], catalog: Sequence[Dict[str, Any]]) -> str:
    subset = [req for req in catalog if req["standard_code"] == spec["standard_code"]]
    if not subset:
        raise ValueError(f"No requirements found for standard {spec['standard_code']}")

    workbook = openpyxl.Workbook()
    readme = workbook.active
    readme.title = "Read_Me"
    _write_readme_sheet(readme, spec, subset)

    sheet = workbook.create_sheet(title="Requirements")
    _write_table_sheet(sheet, REQUIREMENT_COLUMNS, subset)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    workbook.save(path)
    return path


def write_survey_workbook(path: str, questions: Sequence[Dict[str, Any]]) -> str:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Survey_Questions"
    _write_table_sheet(sheet, SURVEY_COLUMNS, questions)

    guide = workbook.create_sheet(title="Read_Me")
    guide_lines = [
        ("Master Discovery Survey", True),
        ("", False),
        ("One question per requirement. req_id is the foreign key into the reference workbooks.", False),
        (f"Questions: {len(questions)}   Sequence: 1 .. {len(questions)}", False),
        ("", False),
        ("phase_category ordering", True),
        ("  1_ARCHITECTURE       what exists: processes, systems, owners", False),
        ("  2_FLOW_AND_LOOPS     how the process actually runs, branches, repeats", False),
        ("  3_DATA_AND_SYSTEMS   data assets, storage, transfer", False),
        ("  4_CIA_PROTECTION     confidentiality, integrity, availability, inheritance", False),
        ("  5_BIA_CONTINUITY     impact analysis, recovery objectives, emergency operation", False),
        ("  6_SECURITY_CONTROLS  management-system and control requirements", False),
        ("", False),
        ("The engine answers from uploaded documents first. Anything it cannot prove from", False),
        ("evidence is escalated to a person with this exact question text.", False),
    ]
    for row_index, (text, is_heading) in enumerate(guide_lines, start=1):
        cell = guide.sheet_properties if False else guide.cell(row=row_index, column=1, value=text)
        cell.font = Font(name="Segoe UI", size=12, bold=True) if is_heading else _BODY_FONT
    guide.column_dimensions["A"].width = 100

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    workbook.save(path)
    return path


# ---------------------------------------------------------------------------
# Excel reading (round-trip)
# ---------------------------------------------------------------------------

def _cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _split_options(value: Any) -> List[str]:
    text = _cell_to_text(value)
    if not text:
        return []
    return [part.strip() for part in text.split(";") if part.strip()]


def load_requirement_catalog(standards_dir: str) -> List[Dict[str, Any]]:
    """Read all four reference workbooks back into one ordered catalog."""
    catalog: List[Dict[str, Any]] = []
    for spec in WORKBOOK_SPECS:
        path = os.path.join(standards_dir, spec["filename"])
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing reference workbook: {path}")
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            if "Requirements" not in workbook.sheetnames:
                raise ValueError(f"{spec['filename']}: missing 'Requirements' sheet")
            sheet = workbook["Requirements"]
            headers = [_cell_to_text(cell) for cell in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))]
            for row in sheet.iter_rows(min_row=2, values_only=True):
                record = {
                    headers[index]: _cell_to_text(value)
                    for index, value in enumerate(row)
                    if index < len(headers) and headers[index]
                }
                if not record.get("req_id"):
                    continue
                record["allowed_options"] = _split_options(record.get("allowed_options"))
                catalog.append(record)
        finally:
            workbook.close()
    return sorted(catalog, key=lambda item: item["req_id"])


def load_survey_catalog(survey_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(survey_path):
        raise FileNotFoundError(f"Missing survey workbook: {survey_path}")
    workbook = openpyxl.load_workbook(survey_path, data_only=True, read_only=True)
    try:
        sheet = workbook["Survey_Questions"]
        headers = [_cell_to_text(cell) for cell in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))]
        questions: List[Dict[str, Any]] = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            record = {
                headers[index]: _cell_to_text(value)
                for index, value in enumerate(row)
                if index < len(headers) and headers[index]
            }
            if not record.get("question_id"):
                continue
            record["allowed_options"] = _split_options(record.get("allowed_options"))
            order = record.get("sequence_order", "")
            record["sequence_order"] = int(order) if str(order).isdigit() else 0
            questions.append(record)
        return sorted(questions, key=lambda item: item["sequence_order"])
    finally:
        workbook.close()


# ---------------------------------------------------------------------------
# Filesystem entry point
# ---------------------------------------------------------------------------

def ensure_standards_and_survey(data_dir: str, force: bool = False) -> Dict[str, Any]:
    """Create (or refresh) the reference workbooks and the survey workbook."""
    standards_dir = os.path.join(data_dir, "standards")
    surveys_dir = os.path.join(data_dir, "surveys")
    os.makedirs(standards_dir, exist_ok=True)
    os.makedirs(surveys_dir, exist_ok=True)

    catalog = build_requirement_catalog()
    questions = build_survey_catalog(catalog)

    written: List[str] = []
    for spec in WORKBOOK_SPECS:
        path = os.path.join(standards_dir, spec["filename"])
        if force or not os.path.exists(path):
            write_standard_workbook(path, spec, catalog)
            written.append(path)

    survey_path = os.path.join(surveys_dir, SURVEY_FILENAME)
    if force or not os.path.exists(survey_path):
        write_survey_workbook(survey_path, questions)
        written.append(survey_path)

    return {
        "standards_dir": standards_dir,
        "survey_path": survey_path,
        "written": written,
        "requirement_count": len(catalog),
        "question_count": len(questions),
        "workbook_paths": standard_workbook_paths(data_dir),
    }


def standard_workbook_paths(data_dir: str) -> Dict[str, str]:
    """Map a safe download token to an absolute workbook path."""
    standards_dir = os.path.join(data_dir, "standards")
    paths: Dict[str, str] = {}
    for spec in WORKBOOK_SPECS:
        token = spec["standard_code"].lower()
        paths[token] = os.path.join(standards_dir, spec["filename"])
    paths["survey"] = os.path.join(data_dir, "surveys", SURVEY_FILENAME)
    return paths


def options_to_json(options: Sequence[str]) -> str:
    return json.dumps(list(options), ensure_ascii=False)
