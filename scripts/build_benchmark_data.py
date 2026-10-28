import os
import json

BENCHMARKS = [
    {
        "id": "municipal-it-access",
        "title": "Municipal IT Access & Privileged Account Provisioning",
        "category": "Critical Infrastructure & Governance",
        "description": "Examines employee onboarding, privileged domain admin access, and role change revocation. Contrasts formal council policy with actual helpdesk operational shortcuts.",
        "formal_policy": """# MUNICIPAL DIRECTIVE SEC-2023-04: ACCESS CONTROL & IDENTITY MANAGEMENT
Issued by: Office of the Chief Information Officer & Data Protection Officer
Applies to: All municipal departments, external contractors, and administrative services

1. ACCESS REQUEST AND APPROVAL (ISO 27002: A.5.15, A.5.18)
All requests for network, email, or application access must be submitted through the Municipal Service Portal with written approval from the designated Department Head and the Data Protection Officer prior to account creation.

2. LEAST PRIVILEGE AND PRIVILEGED ACCOUNTS (ISO 27002: A.8.2, A.8.5)
Administrative credentials (Domain Admin, Database Admin) must only be provisioned to verified systems staff. Multi-Factor Authentication (MFA) via hardware security tokens is strictly mandatory for all administrative access. Privileged accounts must never be shared and must never have internet browsing or email privileges.

3. OFFBOARDING AND ROLE TRANSFERS (ISO 27002: A.5.11, A.5.18, A.6.5)
Human Resources must notify the IT Helpdesk at least 48 hours prior to an employee's transfer or departure. All access credentials must be permanently revoked by 17:00 on the employee's final working day, and physical smartcards must be surrendered to Facilities.""",
        "actual_process": """Helpdesk Operational Journal & Senior Sysadmin Notes:
When a new clerk starts at the Citizen Registration Office, HR often sends the paperwork days late or after the employee is already sitting at the desk. Because citizens are queueing in the lobby and the department head is in meetings, the helpdesk technician usually copies an existing clerk's profile ('Clerk_Template_Group') without waiting for formal DPO sign-off.

For emergency maintenance and weekend server reboots, the IT team shares a single 'svc_muni_admin' account with a password stored in an unencrypted KeePass file on the shared Z: drive. MFA is disabled on this shared account because technicians on call from home use personal phones that cannot run the municipal authenticator software.

When staff transfer between departments (e.g. from Building Permits to Tax Assessment), their old access permissions are never stripped because "they might still need to finish pending citizen files". As a result, veteran staff have accumulated read/write permissions across four separate municipal databases.""",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Definitions_IT_Access"
                  targetNamespace="http://civicprocess.org/bpmn">
  <bpmn:collaboration id="Collaboration_1">
    <bpmn:participant id="Participant_Helpdesk" name="Municipal IT Onboarding &amp; Access Provisioning" processRef="Process_IT_Access" />
  </bpmn:collaboration>
  <bpmn:process id="Process_IT_Access" isExecutable="false">
    <bpmn:laneSet id="LaneSet_1">
      <bpmn:lane id="Lane_HR" name="Human Resources">
        <bpmn:flowNodeRef>StartEvent_Onboarding</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_SubmitHRNotice</bpmn:flowNodeRef>
      </bpmn:lane>
      <bpmn:lane id="Lane_Helpdesk" name="IT Helpdesk / Admin">
        <bpmn:flowNodeRef>Task_CloneExistingUser</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Gateway_AdminNeeded</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_AssignSharedAdmin</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_IssueWorkstation</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>EndEvent_AccessActive</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="StartEvent_Onboarding" name="New Employee Arrives">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_SubmitHRNotice" name="HR Submits Delayed Onboarding Ticket">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_CloneExistingUser" name="Clone Existing Permissions (Skip DPO Sign-off)">
      <bpmn:incoming>Flow_2</bpmn:incoming>
      <bpmn:outgoing>Flow_3</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:exclusiveGateway id="Gateway_AdminNeeded" name="Admin Rights Requested?">
      <bpmn:incoming>Flow_3</bpmn:incoming>
      <bpmn:outgoing>Flow_YesAdmin</bpmn:outgoing>
      <bpmn:outgoing>Flow_NoAdmin</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    <bpmn:userTask id="Task_AssignSharedAdmin" name="Share Unencrypted svc_muni_admin (No MFA)">
      <bpmn:incoming>Flow_YesAdmin</bpmn:incoming>
      <bpmn:outgoing>Flow_4</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_IssueWorkstation" name="Issue Standard Workstation to Staff">
      <bpmn:incoming>Flow_NoAdmin</bpmn:incoming>
      <bpmn:incoming>Flow_4</bpmn:incoming>
      <bpmn:outgoing>Flow_5</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="EndEvent_AccessActive" name="Staff Active on Network">
      <bpmn:incoming>Flow_5</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_Onboarding" targetRef="Task_SubmitHRNotice" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_SubmitHRNotice" targetRef="Task_CloneExistingUser" />
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_CloneExistingUser" targetRef="Gateway_AdminNeeded" />
    <bpmn:sequenceFlow id="Flow_YesAdmin" name="Yes" sourceRef="Gateway_AdminNeeded" targetRef="Task_AssignSharedAdmin" />
    <bpmn:sequenceFlow id="Flow_NoAdmin" name="No" sourceRef="Gateway_AdminNeeded" targetRef="Task_IssueWorkstation" />
    <bpmn:sequenceFlow id="Flow_4" sourceRef="Task_AssignSharedAdmin" targetRef="Task_IssueWorkstation" />
    <bpmn:sequenceFlow id="Flow_5" sourceRef="Task_IssueWorkstation" targetRef="EndEvent_AccessActive" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collaboration_1">
      <bpmndi:BPMNShape id="Participant_Helpdesk_di" bpmnElement="Participant_Helpdesk" isHorizontal="true">
        <dc:Bounds x="160" y="80" width="860" height="380" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Lane_HR_di" bpmnElement="Lane_HR" isHorizontal="true">
        <dc:Bounds x="190" y="80" width="830" height="160" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Lane_Helpdesk_di" bpmnElement="Lane_Helpdesk" isHorizontal="true">
        <dc:Bounds x="190" y="240" width="830" height="220" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="StartEvent_Onboarding_di" bpmnElement="StartEvent_Onboarding">
        <dc:Bounds x="232" y="142" width="36" height="36" />
        <bpmndi:BPMNLabel>
          <dc:Bounds x="214" y="185" width="75" height="27" />
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_SubmitHRNotice_di" bpmnElement="Task_SubmitHRNotice">
        <dc:Bounds x="320" y="120" width="130" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_CloneExistingUser_di" bpmnElement="Task_CloneExistingUser">
        <dc:Bounds x="320" y="280" width="130" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Gateway_AdminNeeded_di" bpmnElement="Gateway_AdminNeeded" isMarkerVisible="true">
        <dc:Bounds x="495" y="295" width="50" height="50" />
        <bpmndi:BPMNLabel>
          <dc:Bounds x="485" y="260" width="70" height="27" />
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_AssignSharedAdmin_di" bpmnElement="Task_AssignSharedAdmin">
        <dc:Bounds x="590" y="270" width="130" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_IssueWorkstation_di" bpmnElement="Task_IssueWorkstation">
        <dc:Bounds x="760" y="320" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="EndEvent_AccessActive_di" bpmnElement="EndEvent_AccessActive">
        <dc:Bounds x="932" y="342" width="36" height="36" />
        <bpmndi:BPMNLabel>
          <dc:Bounds x="918" y="385" width="67" height="27" />
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <di:waypoint x="268" y="160" />
        <di:waypoint x="320" y="160" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2">
        <di:waypoint x="385" y="200" />
        <di:waypoint x="385" y="280" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_3_di" bpmnElement="Flow_3">
        <di:waypoint x="450" y="320" />
        <di:waypoint x="495" y="320" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_YesAdmin_di" bpmnElement="Flow_YesAdmin">
        <di:waypoint x="545" y="320" />
        <di:waypoint x="590" y="310" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_NoAdmin_di" bpmnElement="Flow_NoAdmin">
        <di:waypoint x="520" y="345" />
        <di:waypoint x="520" y="370" />
        <di:waypoint x="760" y="370" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_4_di" bpmnElement="Flow_4">
        <di:waypoint x="720" y="310" />
        <di:waypoint x="740" y="310" />
        <di:waypoint x="740" y="350" />
        <di:waypoint x="760" y="350" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_5_di" bpmnElement="Flow_5">
        <di:waypoint x="880" y="360" />
        <di:waypoint x="932" y="360" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>"""
    },
    {
        "id": "citizen-building-permit",
        "title": "Citizen Building Permit & Architectural Plan Review",
        "category": "Municipal Public Administration & Citizen Services",
        "description": "Reviews the intake, statutory review, fee calculation, and archival of citizen building permit filings. Analyzes PII handling (GDPR Art. 5/6) and storage media protection.",
        "formal_policy": """# MUNICIPAL ADMINISTRATIVE GUIDELINE: DIGITAL BUILDING PERMIT APPLICATION
Issued by: Department of Urban Planning & Infrastructure

1. CITIZEN APPLICATION SUBMISSION (GDPR Art. 6, ISO 27002: A.5.14, A.5.34)
Citizens must submit architectural blueprints, structural engineering calculations, and ownership deeds through the State Verified Citizen Portal. All data must be encrypted with TLS 1.3 in transit.

2. STATUTORY REVIEW & INTER-AGENCY CONSULTATION (ISO 27002: A.5.3, A.5.12)
Case files are routed through the central Electronic Records Management (ERM) system. Reviewing inspectors from the Fire Marshal, Environmental Agency, and Monument Conservation must log approvals digitally.

3. ARCHIVING & RETENTION (ISO 27002: A.5.33, A.8.10)
Approved permits and blueprints must be archived into the WORM (Write-Once-Read-Many) municipal archive with a statutory retention schedule of 50 years.""",
        "actual_process": """Building Office Clerk Field Observations:
The state citizen portal frequently times out when citizens upload architectural CAD blueprints (>50MB). To avoid public complaints, clerks tell citizens on the phone to send their blueprints and deed contracts via regular unencrypted email attachments to the general department mailbox ('bauamt@stadt-verwaltung.local').

Clerks download these citizen attachments to their local C:\\Users\\Public\\Downloads folders. When consulting the Fire Protection inspector, clerks burn the plans onto unencrypted rewriteable DVDs or put them on shared USB sticks because the Fire Marshal is located in a different building without direct access to the municipal ERM network.""",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Definitions_Building_Permit"
                  targetNamespace="http://civicprocess.org/bpmn">
  <bpmn:collaboration id="Collaboration_2">
    <bpmn:participant id="Participant_Building" name="Citizen Building Permit Intake &amp; Field Workflow" processRef="Process_Building_Permit" />
  </bpmn:collaboration>
  <bpmn:process id="Process_Building_Permit" isExecutable="false">
    <bpmn:startEvent id="Start_ApplicationReceived" name="Citizen Submits Permit Application">
      <bpmn:outgoing>Flow_B1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_ReceiveEmailAttachment" name="Receive Plain Email Attachment (Bypass Portal)">
      <bpmn:incoming>Flow_B1</bpmn:incoming>
      <bpmn:outgoing>Flow_B2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_SaveLocalWorkstation" name="Save Blueprints to Local C:\\Drive">
      <bpmn:incoming>Flow_B2</bpmn:incoming>
      <bpmn:outgoing>Flow_B3</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_CopyUnencryptedUSB" name="Copy Plans to Unencrypted USB for Fire Marshal">
      <bpmn:incoming>Flow_B3</bpmn:incoming>
      <bpmn:outgoing>Flow_B4</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_FinalApproval" name="Manual Approval Stamp Issued">
      <bpmn:incoming>Flow_B4</bpmn:incoming>
      <bpmn:outgoing>Flow_B5</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_PermitGranted" name="Permit Mailed to Citizen">
      <bpmn:incoming>Flow_B5</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_B1" sourceRef="Start_ApplicationReceived" targetRef="Task_ReceiveEmailAttachment" />
    <bpmn:sequenceFlow id="Flow_B2" sourceRef="Task_ReceiveEmailAttachment" targetRef="Task_SaveLocalWorkstation" />
    <bpmn:sequenceFlow id="Flow_B3" sourceRef="Task_SaveLocalWorkstation" targetRef="Task_CopyUnencryptedUSB" />
    <bpmn:sequenceFlow id="Flow_B4" sourceRef="Task_CopyUnencryptedUSB" targetRef="Task_FinalApproval" />
    <bpmn:sequenceFlow id="Flow_B5" sourceRef="Task_FinalApproval" targetRef="End_PermitGranted" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_2">
    <bpmndi:BPMNPlane id="BPMNPlane_2" bpmnElement="Collaboration_2">
      <bpmndi:BPMNShape id="Participant_Building_di" bpmnElement="Participant_Building" isHorizontal="true">
        <dc:Bounds x="160" y="80" width="850" height="220" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Start_ApplicationReceived_di" bpmnElement="Start_ApplicationReceived">
        <dc:Bounds x="212" y="172" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_ReceiveEmailAttachment_di" bpmnElement="Task_ReceiveEmailAttachment">
        <dc:Bounds x="290" y="150" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_SaveLocalWorkstation_di" bpmnElement="Task_SaveLocalWorkstation">
        <dc:Bounds x="440" y="150" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_CopyUnencryptedUSB_di" bpmnElement="Task_CopyUnencryptedUSB">
        <dc:Bounds x="590" y="150" width="130" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_FinalApproval_di" bpmnElement="Task_FinalApproval">
        <dc:Bounds x="750" y="150" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="End_PermitGranted_di" bpmnElement="End_PermitGranted">
        <dc:Bounds x="912" y="172" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_B1_di" bpmnElement="Flow_B1">
        <di:waypoint x="248" y="190" />
        <di:waypoint x="290" y="190" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_B2_di" bpmnElement="Flow_B2">
        <di:waypoint x="410" y="190" />
        <di:waypoint x="440" y="190" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_B3_di" bpmnElement="Flow_B3">
        <di:waypoint x="560" y="190" />
        <di:waypoint x="590" y="190" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_B4_di" bpmnElement="Flow_B4">
        <di:waypoint x="720" y="190" />
        <di:waypoint x="750" y="190" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_B5_di" bpmnElement="Flow_B5">
        <di:waypoint x="870" y="190" />
        <di:waypoint x="912" y="190" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>"""
    },
    {
        "id": "critical-water-vendor",
        "title": "Municipal Water Utility Remote Vendor SCADA Maintenance",
        "category": "Critical Infrastructure (KRITIS / NIS2)",
        "description": "Examines external contractor access to telemetry pumps and SCADA control networks. Evaluates ISO 27002 A.5.19 (Supplier relationships) and A.8.20 (Network security).",
        "formal_policy": """# CRITICAL INFRASTRUCTURE MANDATE: REMOTE VENDOR ACCESS TO SCADA / OT
Governed by: NIS2 Directive / KRITIS Municipal Infrastructure Security Group

1. SUPPLIER SECURITY & JUST-IN-TIME SESSIONS (ISO 27002: A.5.19, A.5.20, A.8.2)
All external maintenance vendors connecting to municipal water pumping telemetry must submit a 24-hour advance maintenance notice. Access is granted solely through a monitored Jump Host with recorded sessions and time-boxed MFA tokens.

2. NETWORK SEGREGATION (ISO 27002: A.8.20, A.8.22)
The Operational Technology (OT / SCADA) network must be air-gapped or separated via industrial unidirectional gateways (data diodes) and Next-Gen Firewalls from the municipal administrative corporate network.

3. LOGGING & AUDIT TRAIL (ISO 27002: A.8.15, A.8.16)
All telemetry command executions and vendor terminal inputs must be forwarded in real-time to the Municipal Security Operations Center SIEM repository with 365-day tamper-proof retention.""",
        "actual_process": """Water Treatment Plant Shift Supervisor Log:
During night shifts and emergency pipe bursts, the on-call vendor technician in another city cannot wait 24 hours for the municipal IT department to approve a jump host session.
Three years ago, an emergency cellular 4G modem (AnyDesk / TeamViewer) was plugged directly into the central PLC telemetry switch. The login credentials ('admin / Wasser2021!') are written on a whiteboard inside the pump station.

The vendor connects directly over AnyDesk from their home internet without MFA. The connection bypasses the municipal firewall and SIEM entirely, so no logs exist of when the vendor connects or what PLC setpoints they modify.""",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Definitions_Water_Vendor"
                  targetNamespace="http://civicprocess.org/bpmn">
  <bpmn:collaboration id="Collaboration_3">
    <bpmn:participant id="Participant_Water" name="Municipal Water SCADA Remote Access" processRef="Process_Water_Vendor" />
  </bpmn:collaboration>
  <bpmn:process id="Process_Water_Vendor" isExecutable="false">
    <bpmn:startEvent id="Start_PipeAnomaly" name="Telemetry Pressure Alert Detected">
      <bpmn:outgoing>Flow_W1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_CallVendor" name="Shift Worker Calls Vendor Directly (Bypass Ticket)">
      <bpmn:incoming>Flow_W1</bpmn:incoming>
      <bpmn:outgoing>Flow_W2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_ConnectAnydesk" name="Vendor Connects via Direct 4G AnyDesk (No MFA)">
      <bpmn:incoming>Flow_W2</bpmn:incoming>
      <bpmn:outgoing>Flow_W3</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_DirectPLCControl" name="Vendor Modifies Pump PLC Setpoints (No SIEM Log)">
      <bpmn:incoming>Flow_W3</bpmn:incoming>
      <bpmn:outgoing>Flow_W4</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_PressureStabilized" name="Pressure Stabilized (No Incident Audit Trail)">
      <bpmn:incoming>Flow_W4</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_W1" sourceRef="Start_PipeAnomaly" targetRef="Task_CallVendor" />
    <bpmn:sequenceFlow id="Flow_W2" sourceRef="Task_CallVendor" targetRef="Task_ConnectAnydesk" />
    <bpmn:sequenceFlow id="Flow_W3" sourceRef="Task_ConnectAnydesk" targetRef="Task_DirectPLCControl" />
    <bpmn:sequenceFlow id="Flow_W4" sourceRef="Task_DirectPLCControl" targetRef="End_PressureStabilized" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_3">
    <bpmndi:BPMNPlane id="BPMNPlane_3" bpmnElement="Collaboration_3">
      <bpmndi:BPMNShape id="Participant_Water_di" bpmnElement="Participant_Water" isHorizontal="true">
        <dc:Bounds x="160" y="80" width="750" height="220" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Start_PipeAnomaly_di" bpmnElement="Start_PipeAnomaly">
        <dc:Bounds x="212" y="172" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_CallVendor_di" bpmnElement="Task_CallVendor">
        <dc:Bounds x="290" y="150" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_ConnectAnydesk_di" bpmnElement="Task_ConnectAnydesk">
        <dc:Bounds x="450" y="150" width="130" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_DirectPLCControl_di" bpmnElement="Task_DirectPLCControl">
        <dc:Bounds x="620" y="150" width="130" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="End_PressureStabilized_di" bpmnElement="End_PressureStabilized">
        <dc:Bounds x="802" y="172" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_W1_di" bpmnElement="Flow_W1">
        <di:waypoint x="248" y="190" />
        <di:waypoint x="290" y="190" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_W2_di" bpmnElement="Flow_W2">
        <di:waypoint x="410" y="190" />
        <di:waypoint x="450" y="190" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_W3_di" bpmnElement="Flow_W3">
        <di:waypoint x="580" y="190" />
        <di:waypoint x="620" y="190" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_W4_di" bpmnElement="Flow_W4">
        <di:waypoint x="750" y="190" />
        <di:waypoint x="802" y="190" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>"""
    }
]

def save_benchmark_data(dest_dir: str):
    os.makedirs(dest_dir, exist_ok=True)
    index_file = os.path.join(dest_dir, "benchmarks_index.json")
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(BENCHMARKS, f, indent=2)
    
    for bm in BENCHMARKS:
        bm_dir = os.path.join(dest_dir, bm["id"])
        os.makedirs(bm_dir, exist_ok=True)
        with open(os.path.join(bm_dir, "formal_policy.md"), "w", encoding="utf-8") as f:
            f.write(bm["formal_policy"])
        with open(os.path.join(bm_dir, "actual_process.txt"), "w", encoding="utf-8") as f:
            f.write(bm["actual_process"])
        with open(os.path.join(bm_dir, "model.bpmn"), "w", encoding="utf-8") as f:
            f.write(bm["bpmn_xml"])
            
    print(f"Loaded {len(BENCHMARKS)} real municipal benchmark case studies into: {dest_dir}")

if __name__ == "__main__":
    save_benchmark_data(os.path.join("data", "public_benchmarks"))
