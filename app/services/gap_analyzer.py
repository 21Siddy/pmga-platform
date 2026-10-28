import os
import json
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from app.config import settings

class GapFinding(BaseModel):
    control_id: str = Field(description="ISO 27002 Control ID, e.g. 'A.5.15' or 'A.8.2'")
    control_title: str
    chapter: str
    status: str = Field(description="One of: 'COMPLIANT', 'PARTIAL_GAP', 'CRITICAL_GAP', 'COMPENSATING_CONTROL'")
    policy_claim: str = Field(description="Summary of what official rules/handbooks claimed")
    operational_reality: str = Field(description="What actually happens in day-to-day staff operations")
    plain_english_gap: str = Field(description="Simple, non-technical explanation of the gap and why it matters during an incident")
    actionable_todos: List[str] = Field(description="Concrete step 1, 2, 3 instructions to remediate the gap")
    roles_to_consult: List[str] = Field(description="Specific job titles to involve, e.g. 'Data Protection Officer', 'IT Helpdesk Lead'")
    compensating_controls: List[str] = Field(description="Pragmatic compromise options if strict compliance conflicts with operations")
    affected_bpmn_nodes: List[str] = Field(default=[], description="List of BPMN element IDs directly associated with this gap")

class GapAnalysisResult(BaseModel):
    executive_summary: str
    compliance_score_percent: int
    findings: List[GapFinding]

class GapAnalyzer:
    @staticmethod
    def analyze_process(
        process_text: str,
        policy_text: str,
        bpmn_xml: str,
        selected_controls: List[Dict[str, Any]],
        api_key: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> GapAnalysisResult:
        """
        Cross-analyzes process documentation + BPMN model against ISO 27002 controls.
        Prioritizes psychological safety: explicitly reassures that gaps are normal and healthy,
        and provides pragmatic compensating controls for operational tradeoffs.
        """
        active_key = api_key or settings.GEMINI_API_KEY
        active_model = model_name or settings.DEFAULT_GEMINI_MODEL

        if active_key:
            client = genai.Client(api_key=active_key)
            
            # Format candidate controls for prompt
            controls_summary = "\n".join([
                f"- [{c['control_id']}] {c['title']} ({c['chapter']}): Req: {c['requirement']} | Common municipal gaps: {c.get('common_gaps', '')}"
                for c in selected_controls[:30] # Top candidate controls
            ])

            prompt = f"""You are an elite ISO/IEC 27001 Lead Auditor and GovTech operational resilience expert.
Analyze the following municipal/critical infrastructure process against the provided ISO 27002:2022 controls.

PSYCHOLOGICAL SAFETY PLEDGE:
The organization understands that GAPS ARE OKAY AND EXPECTED. The goal is complete operational transparency—not bureaucratic theater. Never shame the user. Explain risks clearly in PLAIN, ACCESSIBLE ENGLISH so non-technical directors immediately understand what is happening.

COMPROMISE & RISK-BASED DECISION MAKING:
ISO 27001 does NOT require 100% textbook execution if it breaks critical public services. For every gap, provide 2-3 realistic COMPENSATING CONTROLS (pragmatic compromises that balance public service speed with security).

--- LIVED OPERATIONAL REALITY (Staff Actions / Observations):
{process_text}

--- FORMAL POLICY / LEGAL RULES:
{policy_text}

--- BPMN 2.0 MODEL (XML Extract):
{bpmn_xml[:1500]}

--- RELEVANT ISO 27002:2022 CONTROLS TO EVALUATE:
{controls_summary}
"""
            response = client.models.generate_content(
                model=active_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=GapAnalysisResult,
                    temperature=0.2
                )
            )
            return GapAnalysisResult.model_validate_json(response.text)

        # Fallback deterministic analysis
        return GapAnalyzer._generate_fallback_findings(process_text, policy_text, selected_controls)

    @staticmethod
    def _generate_fallback_findings(
        process_text: str,
        policy_text: str,
        controls: List[Dict[str, Any]]
    ) -> GapAnalysisResult:
        """Deterministic heuristic analysis when no LLM key is supplied."""
        findings: List[GapFinding] = []
        p_lower = process_text.lower()
        pol_lower = policy_text.lower()

        # Heuristic 1: MFA / Shared password
        if "password" in p_lower or "share" in p_lower or "no mfa" in p_lower or "keepass" in p_lower or "anydesk" in p_lower:
            findings.append(GapFinding(
                control_id="A.8.2",
                control_title="Privileged access rights",
                chapter="8. Technological controls",
                status="CRITICAL_GAP",
                policy_claim="All administrative access requires unique user accounts and multi-factor authentication (MFA).",
                operational_reality="Staff share a generic administrator account or bypass MFA on personal devices during on-call hours.",
                plain_english_gap="Multiple staff members share the same administrator login without two-factor authentication. If a breach happens or a setting is changed maliciously, the city cannot prove who did it or stop an attacker from taking total control.",
                actionable_todos=[
                    "Issue each technician an individual 'admin-name' account with no email inbox.",
                    "Enforce hardware security keys (FIDO2) or authenticator apps for any privileged login.",
                    "Lock down shared server folders containing plain text passwords."
                ],
                roles_to_consult=["Lead Systems Engineer", "CISO", "IT Helpdesk Supervisor"],
                compensating_controls=[
                    "If field technicians cannot carry company smartphones, deploy dedicated USB hardware security tokens (e.g. YubiKey) for workstation logons.",
                    "Implement a monitored Jump Host bastion where sessions are recorded on video with automatic 4-hour credential rotation."
                ],
                affected_bpmn_nodes=["Task_AssignSharedAdmin", "Task_ConnectAnydesk"]
            ))

        # Heuristic 2: Access review / Offboarding
        if "transfer" in p_lower or "clerk" in p_lower or "strip" in p_lower or "delay" in p_lower:
            findings.append(GapFinding(
                control_id="A.5.18",
                control_title="Access rights",
                chapter="5. Organizational controls",
                status="PARTIAL_GAP",
                policy_claim="Access rights are reviewed quarterly and revoked immediately when an employee transfers or leaves.",
                operational_reality="Permissions from previous departmental roles are left active because staff 'might still need them'.",
                plain_english_gap="Employees moving between departments keep their old database access. Over time, employees accumulate rights to sensitive records across the entire city hall that they no longer have any legal reason to see.",
                actionable_todos=[
                    "Enforce a mandatory account re-certification ticket whenever HR processes an internal job transfer.",
                    "Review all active Active Directory group memberships for clerks transferred in the past 12 months."
                ],
                roles_to_consult=["Human Resources Director", "Department Heads", "Data Protection Officer (DPO)"],
                compensating_controls=[
                    "If historical access is temporarily needed for pending cases, grant time-boxed 14-day read-only delegation that expires automatically."
                ],
                affected_bpmn_nodes=["Task_CloneExistingUser"]
            ))

        # Heuristic 3: Unencrypted data transfer / USB
        if "usb" in p_lower or "email" in p_lower or "attachment" in p_lower or "unencrypted" in p_lower:
            findings.append(GapFinding(
                control_id="A.5.14",
                control_title="Information transfer",
                chapter="5. Organizational controls",
                status="CRITICAL_GAP",
                policy_claim="All citizen documents and records must be transferred through encrypted state portals.",
                operational_reality="Staff download citizen files to local drives and transfer them via regular unencrypted email attachments or thumb drives.",
                plain_english_gap="Citizen documents containing private personal data and architectural blueprints are sent via ordinary email or unencrypted USB drives. These can be intercepted or lost without any audit record.",
                actionable_todos=[
                    "Provide a secure municipal web upload link for large citizen architectural files.",
                    "Distribute hardware-encrypted USB drives (AES-256) and block unencrypted USB write access via central policy."
                ],
                roles_to_consult=["Data Protection Officer (DPO)", "Facilities / Building Office Lead", "IT Infrastructure Lead"],
                compensating_controls=[
                    "Enable automated password-protected 7-Zip file encryption with SMS password exchange for urgent contractor files."
                ],
                affected_bpmn_nodes=["Task_ReceiveEmailAttachment", "Task_CopyUnencryptedUSB"]
            ))

        score = max(20, 100 - (len(findings) * 25))
        return GapAnalysisResult(
            executive_summary=f"Automated evaluation identified {len(findings)} operational gaps where lived staff practice deviates from ISO 27002 controls. Pragmatic compensating controls are provided to protect critical municipal workflows.",
            compliance_score_percent=score,
            findings=findings
        )
