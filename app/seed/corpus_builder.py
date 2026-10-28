import os
import json
from typing import Dict, List, Any
from app.services.bpmn_parser import BPMNParser
from app.services.bpmn_engine import BPMNEngine

# 12 Curated Ground-Truth BPMN 2.0 Models across 5 Structural Archetypes
CORPUS_MODELS = [
    # --- Archetype A: Linear Multi-Lane Handoffs ---
    {
        "id": "01_citizen_certificate_request",
        "title": "Citizen Birth Certificate Issuance",
        "archetype": "Linear Multi-Lane Handoff",
        "reference": "Public Administration Standard (E-Government Core)",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Def_01" targetNamespace="http://pmga.local/bpmn">
  <bpmn:collaboration id="Collab_01">
    <bpmn:participant id="Part_01" name="Citizen Birth Certificate Issuance" processRef="Proc_01" />
  </bpmn:collaboration>
  <bpmn:process id="Proc_01" isExecutable="false">
    <bpmn:laneSet id="LaneSet_01">
      <bpmn:lane id="Lane_Citizen" name="Citizen">
        <bpmn:flowNodeRef>Start_CitizenApp</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_SubmitForm</bpmn:flowNodeRef>
      </bpmn:lane>
      <bpmn:lane id="Lane_Clerk" name="Front Desk Clerk">
        <bpmn:flowNodeRef>Task_VerifyID</bpmn:flowNodeRef>
      </bpmn:lane>
      <bpmn:lane id="Lane_Archivist" name="Municipal Archivist">
        <bpmn:flowNodeRef>Task_PrintCertificate</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>End_CertificateIssued</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="Start_CitizenApp" name="Citizen Needs Certificate">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_SubmitForm" name="Submit Identity Request and Fee">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_VerifyID" name="Verify Citizen Identity and Ledger Entry">
      <bpmn:incoming>Flow_2</bpmn:incoming>
      <bpmn:outgoing>Flow_3</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_PrintCertificate" name="Print Official Stamped Certificate">
      <bpmn:incoming>Flow_3</bpmn:incoming>
      <bpmn:outgoing>Flow_4</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_CertificateIssued" name="Certificate Delivered to Citizen">
      <bpmn:incoming>Flow_4</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_CitizenApp" targetRef="Task_SubmitForm" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_SubmitForm" targetRef="Task_VerifyID" />
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_VerifyID" targetRef="Task_PrintCertificate" />
    <bpmn:sequenceFlow id="Flow_4" sourceRef="Task_PrintCertificate" targetRef="End_CertificateIssued" />
  </bpmn:process>
</bpmn:definitions>"""
    },
    {
        "id": "02_it_workstation_rollout",
        "title": "Standard Workstation Provisioning",
        "archetype": "Linear Multi-Lane Handoff",
        "reference": "ITIL Service Desk Reference",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Def_02" targetNamespace="http://pmga.local/bpmn">
  <bpmn:collaboration id="Collab_02">
    <bpmn:participant id="Part_02" name="Standard Workstation Provisioning" processRef="Proc_02" />
  </bpmn:collaboration>
  <bpmn:process id="Proc_02" isExecutable="false">
    <bpmn:laneSet id="LaneSet_02">
      <bpmn:lane id="Lane_HR" name="Human Resources">
        <bpmn:flowNodeRef>Start_NewHire</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_NotifyIT</bpmn:flowNodeRef>
      </bpmn:lane>
      <bpmn:lane id="Lane_IT" name="IT Operations">
        <bpmn:flowNodeRef>Task_ImageLaptop</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_DeliverLaptop</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>End_WorkstationReady</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="Start_NewHire" name="New Employee Scheduled">
      <bpmn:outgoing>Flow_02_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_NotifyIT" name="Send Onboarding Hardware Ticket">
      <bpmn:incoming>Flow_02_1</bpmn:incoming>
      <bpmn:outgoing>Flow_02_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_ImageLaptop" name="Install Hardened OS and Security Suite">
      <bpmn:incoming>Flow_02_2</bpmn:incoming>
      <bpmn:outgoing>Flow_02_3</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_DeliverLaptop" name="Hand Over Laptop and Sign Receipt">
      <bpmn:incoming>Flow_02_3</bpmn:incoming>
      <bpmn:outgoing>Flow_02_4</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_WorkstationReady" name="Laptop Operational">
      <bpmn:incoming>Flow_02_4</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_02_1" sourceRef="Start_NewHire" targetRef="Task_NotifyIT" />
    <bpmn:sequenceFlow id="Flow_02_2" sourceRef="Task_NotifyIT" targetRef="Task_ImageLaptop" />
    <bpmn:sequenceFlow id="Flow_02_3" sourceRef="Task_ImageLaptop" targetRef="Task_DeliverLaptop" />
    <bpmn:sequenceFlow id="Flow_02_4" sourceRef="Task_DeliverLaptop" targetRef="End_WorkstationReady" />
  </bpmn:process>
</bpmn:definitions>"""
    },

    # --- Archetype B: Exclusive Decision Branching (XOR) ---
    {
        "id": "03_invoice_approval_xor",
        "title": "Vendor Invoice Approval Flow",
        "archetype": "Exclusive Decision Branching (XOR)",
        "reference": "Camunda Official Reference Example",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Def_03" targetNamespace="http://pmga.local/bpmn">
  <bpmn:collaboration id="Collab_03">
    <bpmn:participant id="Part_03" name="Vendor Invoice Approval Flow" processRef="Proc_03" />
  </bpmn:collaboration>
  <bpmn:process id="Proc_03" isExecutable="false">
    <bpmn:startEvent id="Start_Invoice" name="Invoice Received">
      <bpmn:outgoing>F_03_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_CheckAmount" name="Verify Invoice Line Items">
      <bpmn:incoming>F_03_1</bpmn:incoming>
      <bpmn:outgoing>F_03_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:exclusiveGateway id="Gateway_AmountXOR" name="Amount &gt; 5000 EUR?">
      <bpmn:incoming>F_03_2</bpmn:incoming>
      <bpmn:outgoing>F_YesMgr</bpmn:outgoing>
      <bpmn:outgoing>F_NoAuto</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    <bpmn:userTask id="Task_ManagerSign" name="Obtain Department Head Signature">
      <bpmn:incoming>F_YesMgr</bpmn:incoming>
      <bpmn:outgoing>F_03_3</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_ExecutePayment" name="Execute Bank Wire Transfer">
      <bpmn:incoming>F_NoAuto</bpmn:incoming>
      <bpmn:incoming>F_03_3</bpmn:incoming>
      <bpmn:outgoing>F_03_4</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_Paid" name="Payment Settled">
      <bpmn:incoming>F_03_4</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_03_1" sourceRef="Start_Invoice" targetRef="Task_CheckAmount" />
    <bpmn:sequenceFlow id="F_03_2" sourceRef="Task_CheckAmount" targetRef="Gateway_AmountXOR" />
    <bpmn:sequenceFlow id="F_YesMgr" name="Yes" sourceRef="Gateway_AmountXOR" targetRef="Task_ManagerSign" />
    <bpmn:sequenceFlow id="F_NoAuto" name="No" sourceRef="Gateway_AmountXOR" targetRef="Task_ExecutePayment" />
    <bpmn:sequenceFlow id="F_03_3" sourceRef="Task_ManagerSign" targetRef="Task_ExecutePayment" />
    <bpmn:sequenceFlow id="F_03_4" sourceRef="Task_ExecutePayment" targetRef="End_Paid" />
  </bpmn:process>
</bpmn:definitions>"""
    },
    {
        "id": "04_building_permit_triage",
        "title": "Building Permit Structural Triage",
        "archetype": "Exclusive Decision Branching (XOR)",
        "reference": "Municipal Planning Code",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  id="Def_04" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_04" name="Building Permit Structural Triage" isExecutable="false">
    <bpmn:startEvent id="Start_Permit" name="Blueprint Submitted">
      <bpmn:outgoing>F_04_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_Triage" name="Inspect Cadastral Plot Boundary">
      <bpmn:incoming>F_04_1</bpmn:incoming>
      <bpmn:outgoing>F_04_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:exclusiveGateway id="Gateway_CommercialXOR" name="Commercial Project?">
      <bpmn:incoming>F_04_2</bpmn:incoming>
      <bpmn:outgoing>F_CommYes</bpmn:outgoing>
      <bpmn:outgoing>F_CommNo</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    <bpmn:userTask id="Task_FireMarshal" name="Conduct Fire Marshal Hearing">
      <bpmn:incoming>F_CommYes</bpmn:incoming>
      <bpmn:outgoing>F_04_3</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_StandardReview" name="Standard Administrative Review">
      <bpmn:incoming>F_CommNo</bpmn:incoming>
      <bpmn:outgoing>F_04_4</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_PermitDone" name="Permit Decision Issued">
      <bpmn:incoming>F_04_3</bpmn:incoming>
      <bpmn:incoming>F_04_4</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_04_1" sourceRef="Start_Permit" targetRef="Task_Triage" />
    <bpmn:sequenceFlow id="F_04_2" sourceRef="Task_Triage" targetRef="Gateway_CommercialXOR" />
    <bpmn:sequenceFlow id="F_CommYes" name="Yes" sourceRef="Gateway_CommercialXOR" targetRef="Task_FireMarshal" />
    <bpmn:sequenceFlow id="F_CommNo" name="No" sourceRef="Gateway_CommercialXOR" targetRef="Task_StandardReview" />
    <bpmn:sequenceFlow id="F_04_3" sourceRef="Task_FireMarshal" targetRef="End_PermitDone" />
    <bpmn:sequenceFlow id="F_04_4" sourceRef="Task_StandardReview" targetRef="End_PermitDone" />
  </bpmn:process>
</bpmn:definitions>"""
    },
    {
        "id": "05_access_request_vetting",
        "title": "Privileged Role Request Vetting",
        "archetype": "Exclusive Decision Branching (XOR)",
        "reference": "ISO/IEC 27002 Control A.8.2 Reference",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="Def_05" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_05" name="Privileged Role Request Vetting" isExecutable="false">
    <bpmn:startEvent id="Start_Req" name="Access Ticket Logged">
      <bpmn:outgoing>F_05_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:exclusiveGateway id="Gateway_AdminXOR" name="Root Access Needed?">
      <bpmn:incoming>F_05_1</bpmn:incoming>
      <bpmn:outgoing>F_AdminYes</bpmn:outgoing>
      <bpmn:outgoing>F_AdminNo</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    <bpmn:userTask id="Task_DPOSignoff" name="Require Written DPO Sign-Off">
      <bpmn:incoming>F_AdminYes</bpmn:incoming>
      <bpmn:outgoing>F_05_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_ProvisionRole" name="Add User to Target Active Directory Role">
      <bpmn:incoming>F_AdminNo</bpmn:incoming>
      <bpmn:incoming>F_05_2</bpmn:incoming>
      <bpmn:outgoing>F_05_3</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_AccessGranted" name="Credentials Active">
      <bpmn:incoming>F_05_3</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_05_1" sourceRef="Start_Req" targetRef="Gateway_AdminXOR" />
    <bpmn:sequenceFlow id="F_AdminYes" name="Yes" sourceRef="Gateway_AdminXOR" targetRef="Task_DPOSignoff" />
    <bpmn:sequenceFlow id="F_AdminNo" name="No" sourceRef="Gateway_AdminXOR" targetRef="Task_ProvisionRole" />
    <bpmn:sequenceFlow id="F_05_2" sourceRef="Task_DPOSignoff" targetRef="Task_ProvisionRole" />
    <bpmn:sequenceFlow id="F_05_3" sourceRef="Task_ProvisionRole" targetRef="End_AccessGranted" />
  </bpmn:process>
</bpmn:definitions>"""
    },

    # --- Archetype C: Parallel Concurrency (AND Gateways) [Lê et al., 2025] ---
    {
        "id": "06_parallel_employee_onboarding",
        "title": "Parallel Departmental Employee Onboarding",
        "archetype": "Parallel Concurrency (AND)",
        "reference": "PET & Lê et al. (2025) Parallel Gateway Benchmark",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="Def_06" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_06" name="Parallel Departmental Employee Onboarding" isExecutable="false">
    <bpmn:startEvent id="Start_Hire" name="Contract Signed">
      <bpmn:outgoing>F_06_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:parallelGateway id="Gateway_SplitAND" name="Initiate Parallel Onboarding">
      <bpmn:incoming>F_06_1</bpmn:incoming>
      <bpmn:outgoing>F_ToIT</bpmn:outgoing>
      <bpmn:outgoing>F_ToHR</bpmn:outgoing>
      <bpmn:outgoing>F_ToFac</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Task_SetupIT" name="Provision Email and VPN Credentials">
      <bpmn:incoming>F_ToIT</bpmn:incoming>
      <bpmn:outgoing>F_JoinIT</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_SetupHR" name="Enroll in Health Insurance and Payroll">
      <bpmn:incoming>F_ToHR</bpmn:incoming>
      <bpmn:outgoing>F_JoinHR</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_SetupBadge" name="Program Building Access Smartcard">
      <bpmn:incoming>F_ToFac</bpmn:incoming>
      <bpmn:outgoing>F_JoinFac</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:parallelGateway id="Gateway_JoinAND" name="Synchronize Onboarding Steps">
      <bpmn:incoming>F_JoinIT</bpmn:incoming>
      <bpmn:incoming>F_JoinHR</bpmn:incoming>
      <bpmn:incoming>F_JoinFac</bpmn:incoming>
      <bpmn:outgoing>F_ToFinal</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Task_Orientation" name="Conduct First-Day Welcome Orientation">
      <bpmn:incoming>F_ToFinal</bpmn:incoming>
      <bpmn:outgoing>F_06_End</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_Onboarded" name="Employee Fully Active">
      <bpmn:incoming>F_06_End</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_06_1" sourceRef="Start_Hire" targetRef="Gateway_SplitAND" />
    <bpmn:sequenceFlow id="F_ToIT" sourceRef="Gateway_SplitAND" targetRef="Task_SetupIT" />
    <bpmn:sequenceFlow id="F_ToHR" sourceRef="Gateway_SplitAND" targetRef="Task_SetupHR" />
    <bpmn:sequenceFlow id="F_ToFac" sourceRef="Gateway_SplitAND" targetRef="Task_SetupBadge" />
    <bpmn:sequenceFlow id="F_JoinIT" sourceRef="Task_SetupIT" targetRef="Gateway_JoinAND" />
    <bpmn:sequenceFlow id="F_JoinHR" sourceRef="Task_SetupHR" targetRef="Gateway_JoinAND" />
    <bpmn:sequenceFlow id="F_JoinFac" sourceRef="Task_SetupBadge" targetRef="Gateway_JoinAND" />
    <bpmn:sequenceFlow id="F_ToFinal" sourceRef="Gateway_JoinAND" targetRef="Task_Orientation" />
    <bpmn:sequenceFlow id="F_06_End" sourceRef="Task_Orientation" targetRef="End_Onboarded" />
  </bpmn:process>
</bpmn:definitions>"""
    },
    {
        "id": "07_emergency_incident_response_and",
        "title": "Critical Infrastructure Incident Response",
        "archetype": "Parallel Concurrency (AND)",
        "reference": "NIS2 & KRITIS Cyber Incident Guidelines",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="Def_07" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_07" name="Critical Infrastructure Incident Response" isExecutable="false">
    <bpmn:startEvent id="Start_CyberAlert" name="Intrusion Detected on Telemetry Network">
      <bpmn:outgoing>F_07_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:parallelGateway id="Gateway_EmergencySplit" name="Parallel Containment Actions">
      <bpmn:incoming>F_07_1</bpmn:incoming>
      <bpmn:outgoing>F_IsolateVLAN</bpmn:outgoing>
      <bpmn:outgoing>F_NotifyCERT</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Task_IsolateSubnet" name="Sever Physical Uplink to SCADA Subnet">
      <bpmn:incoming>F_IsolateVLAN</bpmn:incoming>
      <bpmn:outgoing>F_JoinIso</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_NotifyAgency" name="Submit Urgent Regulatory Alert to BSI / CISA">
      <bpmn:incoming>F_NotifyCERT</bpmn:incoming>
      <bpmn:outgoing>F_JoinNotif</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:parallelGateway id="Gateway_EmergencyJoin" name="Merge Incident Containment">
      <bpmn:incoming>F_JoinIso</bpmn:incoming>
      <bpmn:incoming>F_JoinNotif</bpmn:incoming>
      <bpmn:outgoing>F_ToForensics</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Task_PreserveLogs" name="Capture Cryptographic Disk Images for Forensics">
      <bpmn:incoming>F_ToForensics</bpmn:incoming>
      <bpmn:outgoing>F_07_End</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_Contained" name="Threat Fully Contained">
      <bpmn:incoming>F_07_End</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_07_1" sourceRef="Start_CyberAlert" targetRef="Gateway_EmergencySplit" />
    <bpmn:sequenceFlow id="F_IsolateVLAN" sourceRef="Gateway_EmergencySplit" targetRef="Task_IsolateSubnet" />
    <bpmn:sequenceFlow id="F_NotifyCERT" sourceRef="Gateway_EmergencySplit" targetRef="Task_NotifyAgency" />
    <bpmn:sequenceFlow id="F_JoinIso" sourceRef="Task_IsolateSubnet" targetRef="Gateway_EmergencyJoin" />
    <bpmn:sequenceFlow id="F_JoinNotif" sourceRef="Task_NotifyAgency" targetRef="Gateway_EmergencyJoin" />
    <bpmn:sequenceFlow id="F_ToForensics" sourceRef="Gateway_EmergencyJoin" targetRef="Task_PreserveLogs" />
    <bpmn:sequenceFlow id="F_07_End" sourceRef="Task_PreserveLogs" targetRef="End_Contained" />
  </bpmn:process>
</bpmn:definitions>"""
    },
    {
        "id": "08_procurement_dual_review_and",
        "title": "Vendor Contract Dual-Review Gate",
        "archetype": "Parallel Concurrency (AND)",
        "reference": "Public Sector Tender Compliance",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="Def_08" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_08" name="Vendor Contract Dual-Review Gate" isExecutable="false">
    <bpmn:startEvent id="Start_Tender" name="Vendor Bid Received">
      <bpmn:outgoing>F_08_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:parallelGateway id="Gateway_ReviewSplit" name="Concurrent Departmental Reviews">
      <bpmn:incoming>F_08_1</bpmn:incoming>
      <bpmn:outgoing>F_Legal</bpmn:outgoing>
      <bpmn:outgoing>F_Security</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Task_LegalReview" name="Audit GDPR Art 28 Data Processing Terms">
      <bpmn:incoming>F_Legal</bpmn:incoming>
      <bpmn:outgoing>F_LegalDone</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_SecurityReview" name="Verify ISO 27001 SOC2 Certification">
      <bpmn:incoming>F_Security</bpmn:incoming>
      <bpmn:outgoing>F_SecDone</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:parallelGateway id="Gateway_ReviewJoin" name="Dual-Signoff Synchronized">
      <bpmn:incoming>F_LegalDone</bpmn:incoming>
      <bpmn:incoming>F_SecDone</bpmn:incoming>
      <bpmn:outgoing>F_ToAward</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Task_SignContract" name="Execute Public Procurement Agreement">
      <bpmn:incoming>F_ToAward</bpmn:incoming>
      <bpmn:outgoing>F_08_End</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_Awarded" name="Vendor Onboarded">
      <bpmn:incoming>F_08_End</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_08_1" sourceRef="Start_Tender" targetRef="Gateway_ReviewSplit" />
    <bpmn:sequenceFlow id="F_Legal" sourceRef="Gateway_ReviewSplit" targetRef="Task_LegalReview" />
    <bpmn:sequenceFlow id="F_Security" sourceRef="Gateway_ReviewSplit" targetRef="Task_SecurityReview" />
    <bpmn:sequenceFlow id="F_LegalDone" sourceRef="Task_LegalReview" targetRef="Gateway_ReviewJoin" />
    <bpmn:sequenceFlow id="F_SecDone" sourceRef="Task_SecurityReview" targetRef="Gateway_ReviewJoin" />
    <bpmn:sequenceFlow id="F_ToAward" sourceRef="Gateway_ReviewJoin" targetRef="Task_SignContract" />
    <bpmn:sequenceFlow id="F_08_End" sourceRef="Task_SignContract" targetRef="End_Awarded" />
  </bpmn:process>
</bpmn:definitions>"""
    },

    # --- Archetype D: Cyclic Rework Loops & Escalations ---
    {
        "id": "09_document_verification_loop",
        "title": "Application Completeness Check and Rework Loop",
        "archetype": "Cyclic Rework Loop",
        "reference": "Public Administration Quality Control Loop",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="Def_09" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_09" name="Application Completeness Check and Rework Loop" isExecutable="false">
    <bpmn:startEvent id="Start_Filing" name="Citizen Submits Application">
      <bpmn:outgoing>F_09_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_CheckDocs" name="Review Attached Identification Documents">
      <bpmn:incoming>F_09_1</bpmn:incoming>
      <bpmn:incoming>F_ReworkLoop</bpmn:incoming>
      <bpmn:outgoing>F_09_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:exclusiveGateway id="Gateway_CompleteXOR" name="Documents Valid?">
      <bpmn:incoming>F_09_2</bpmn:incoming>
      <bpmn:outgoing>F_ValidYes</bpmn:outgoing>
      <bpmn:outgoing>F_ValidNo</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    <bpmn:userTask id="Task_RequestCorrection" name="Issue Defect Notice and Request Correction">
      <bpmn:incoming>F_ValidNo</bpmn:incoming>
      <bpmn:outgoing>F_ReworkLoop</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_ApproveFiling" name="Grant Final Stamp of Official Approval">
      <bpmn:incoming>F_ValidYes</bpmn:incoming>
      <bpmn:outgoing>F_09_End</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_Permitted" name="Filing Completed">
      <bpmn:incoming>F_09_End</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_09_1" sourceRef="Start_Filing" targetRef="Task_CheckDocs" />
    <bpmn:sequenceFlow id="F_09_2" sourceRef="Task_CheckDocs" targetRef="Gateway_CompleteXOR" />
    <bpmn:sequenceFlow id="F_ValidNo" name="Defective" sourceRef="Gateway_CompleteXOR" targetRef="Task_RequestCorrection" />
    <bpmn:sequenceFlow id="F_ReworkLoop" sourceRef="Task_RequestCorrection" targetRef="Task_CheckDocs" />
    <bpmn:sequenceFlow id="F_ValidYes" name="Valid" sourceRef="Gateway_CompleteXOR" targetRef="Task_ApproveFiling" />
    <bpmn:sequenceFlow id="F_09_End" sourceRef="Task_ApproveFiling" targetRef="End_Permitted" />
  </bpmn:process>
</bpmn:definitions>"""
    },
    {
        "id": "10_change_management_testing_loop",
        "title": "IT Change Security Testing Loop",
        "archetype": "Cyclic Rework Loop",
        "reference": "ISO 27002 A.8.32 Change Management Protocol",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="Def_10" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_10" name="IT Change Security Testing Loop" isExecutable="false">
    <bpmn:startEvent id="Start_Change" name="Patch Prepared by Developers">
      <bpmn:outgoing>F_10_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_RunScan" name="Deploy to Staging and Execute SAST Vulnerability Scan">
      <bpmn:incoming>F_10_1</bpmn:incoming>
      <bpmn:incoming>F_FixLoop</bpmn:incoming>
      <bpmn:outgoing>F_10_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:exclusiveGateway id="Gateway_ScanXOR" name="High CVEs Found?">
      <bpmn:incoming>F_10_2</bpmn:incoming>
      <bpmn:outgoing>F_CveYes</bpmn:outgoing>
      <bpmn:outgoing>F_CveNo</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    <bpmn:userTask id="Task_RemediateBug" name="Remediate Insecure Code and Recompile">
      <bpmn:incoming>F_CveYes</bpmn:incoming>
      <bpmn:outgoing>F_FixLoop</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_PromoteProd" name="Promote Patch to Production Cluster">
      <bpmn:incoming>F_CveNo</bpmn:incoming>
      <bpmn:outgoing>F_10_End</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_Live" name="Patch Live in Production">
      <bpmn:incoming>F_10_End</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_10_1" sourceRef="Start_Change" targetRef="Task_RunScan" />
    <bpmn:sequenceFlow id="F_10_2" sourceRef="Task_RunScan" targetRef="Gateway_ScanXOR" />
    <bpmn:sequenceFlow id="F_CveYes" name="Yes" sourceRef="Gateway_ScanXOR" targetRef="Task_RemediateBug" />
    <bpmn:sequenceFlow id="F_FixLoop" sourceRef="Task_RemediateBug" targetRef="Task_RunScan" />
    <bpmn:sequenceFlow id="F_CveNo" name="Clean" sourceRef="Gateway_ScanXOR" targetRef="Task_PromoteProd" />
    <bpmn:sequenceFlow id="F_10_End" sourceRef="Task_PromoteProd" targetRef="End_Live" />
  </bpmn:process>
</bpmn:definitions>"""
    },

    # --- Archetype E: Complex Hybrid Processes (XOR + AND + Multi-Lane) ---
    {
        "id": "11_scada_vendor_maintenance_hybrid",
        "title": "Industrial SCADA Vendor Maintenance",
        "archetype": "Complex Hybrid (XOR + AND)",
        "reference": "Municipal Water Utility KRITIS Standard",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="Def_11" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_11" name="Industrial SCADA Vendor Maintenance" isExecutable="false">
    <bpmn:startEvent id="Start_Alarm" name="Telemetry Pressure Spike Detected">
      <bpmn:outgoing>F_11_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_AssessSeverity" name="Assess Pipeline Failure Probability">
      <bpmn:incoming>F_11_1</bpmn:incoming>
      <bpmn:outgoing>F_11_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:exclusiveGateway id="Gateway_CritXOR" name="Critical Emergency?">
      <bpmn:incoming>F_11_2</bpmn:incoming>
      <bpmn:outgoing>F_EmergYes</bpmn:outgoing>
      <bpmn:outgoing>F_EmergNo</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    <bpmn:parallelGateway id="Gateway_DualOpsAND" name="Emergency Dual Measures">
      <bpmn:incoming>F_EmergYes</bpmn:incoming>
      <bpmn:outgoing>F_EngageVendor</bpmn:outgoing>
      <bpmn:outgoing>F_DivertFlow</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Task_RemoteFix" name="Connect External Vendor via Bastion Session">
      <bpmn:incoming>F_EngageVendor</bpmn:incoming>
      <bpmn:outgoing>F_JoinV</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_OpenBypassValve" name="Manually Open Secondary Pressure Bypass">
      <bpmn:incoming>F_DivertFlow</bpmn:incoming>
      <bpmn:outgoing>F_JoinP</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:parallelGateway id="Gateway_DualOpsJoin" name="Synchronize Stabilized State">
      <bpmn:incoming>F_JoinV</bpmn:incoming>
      <bpmn:incoming>F_JoinP</bpmn:incoming>
      <bpmn:outgoing>F_MergedFix</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Task_RoutineService" name="Schedule Standard Daytime Maintenance Ticket">
      <bpmn:incoming>F_EmergNo</bpmn:incoming>
      <bpmn:outgoing>F_RoutineDone</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_WaterStable" name="Pressure Stabilized">
      <bpmn:incoming>F_MergedFix</bpmn:incoming>
      <bpmn:incoming>F_RoutineDone</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_11_1" sourceRef="Start_Alarm" targetRef="Task_AssessSeverity" />
    <bpmn:sequenceFlow id="F_11_2" sourceRef="Task_AssessSeverity" targetRef="Gateway_CritXOR" />
    <bpmn:sequenceFlow id="F_EmergYes" name="Critical" sourceRef="Gateway_CritXOR" targetRef="Gateway_DualOpsAND" />
    <bpmn:sequenceFlow id="F_EmergNo" name="Minor" sourceRef="Gateway_CritXOR" targetRef="Task_RoutineService" />
    <bpmn:sequenceFlow id="F_EngageVendor" sourceRef="Gateway_DualOpsAND" targetRef="Task_RemoteFix" />
    <bpmn:sequenceFlow id="F_DivertFlow" sourceRef="Gateway_DualOpsAND" targetRef="Task_OpenBypassValve" />
    <bpmn:sequenceFlow id="F_JoinV" sourceRef="Task_RemoteFix" targetRef="Gateway_DualOpsJoin" />
    <bpmn:sequenceFlow id="F_JoinP" sourceRef="Task_OpenBypassValve" targetRef="Gateway_DualOpsJoin" />
    <bpmn:sequenceFlow id="F_MergedFix" sourceRef="Gateway_DualOpsJoin" targetRef="End_WaterStable" />
    <bpmn:sequenceFlow id="F_RoutineDone" sourceRef="Task_RoutineService" targetRef="End_WaterStable" />
  </bpmn:process>
</bpmn:definitions>"""
    },
    {
        "id": "12_gdpr_data_breach_notification",
        "title": "GDPR 72-Hour Data Breach Incident Workflow",
        "archetype": "Complex Hybrid (XOR + AND)",
        "reference": "GDPR Articles 33 & 34 Compliance Workflow",
        "bpmn_xml": """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  id="Def_12" targetNamespace="http://pmga.local/bpmn">
  <bpmn:process id="Proc_12" name="GDPR 72-Hour Data Breach Incident Workflow" isExecutable="false">
    <bpmn:startEvent id="Start_Leak" name="Citizen Database Leak Discovered">
      <bpmn:outgoing>F_12_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:parallelGateway id="Gateway_BreachSplit" name="Parallel Triage Actions">
      <bpmn:incoming>F_12_1</bpmn:incoming>
      <bpmn:outgoing>F_TriageDPO</bpmn:outgoing>
      <bpmn:outgoing>F_TriageSec</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Task_AssessRisk" name="DPO Evaluates Severity of Rights and Freedoms">
      <bpmn:incoming>F_TriageDPO</bpmn:incoming>
      <bpmn:outgoing>F_DpoDone</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_ContainSource" name="Security Engineers Revoke Compromised API Token">
      <bpmn:incoming>F_TriageSec</bpmn:incoming>
      <bpmn:outgoing>F_SecDone</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:parallelGateway id="Gateway_BreachJoin" name="Triage Complete">
      <bpmn:incoming>F_DpoDone</bpmn:incoming>
      <bpmn:incoming>F_SecDone</bpmn:incoming>
      <bpmn:outgoing>F_ToEval</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:exclusiveGateway id="Gateway_ReportXOR" name="High Risk to Citizens?">
      <bpmn:incoming>F_ToEval</bpmn:incoming>
      <bpmn:outgoing>F_NotifyAll</bpmn:outgoing>
      <bpmn:outgoing>F_LogOnly</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    <bpmn:userTask id="Task_NotifyRegulator" name="File Official Report to Data Protection Authority and Email Affected Citizens">
      <bpmn:incoming>F_NotifyAll</bpmn:incoming>
      <bpmn:outgoing>F_DoneNotif</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:userTask id="Task_InternalRecord" name="Record Internal Breach Log in Incident Register">
      <bpmn:incoming>F_LogOnly</bpmn:incoming>
      <bpmn:outgoing>F_DoneLog</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="End_BreachClosed" name="Incident Regulatory Obligations Fulfilled">
      <bpmn:incoming>F_DoneNotif</bpmn:incoming>
      <bpmn:incoming>F_DoneLog</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="F_12_1" sourceRef="Start_Leak" targetRef="Gateway_BreachSplit" />
    <bpmn:sequenceFlow id="F_TriageDPO" sourceRef="Gateway_BreachSplit" targetRef="Task_AssessRisk" />
    <bpmn:sequenceFlow id="F_TriageSec" sourceRef="Gateway_BreachSplit" targetRef="Task_ContainSource" />
    <bpmn:sequenceFlow id="F_DpoDone" sourceRef="Task_AssessRisk" targetRef="Gateway_BreachJoin" />
    <bpmn:sequenceFlow id="F_SecDone" sourceRef="Task_ContainSource" targetRef="Gateway_BreachJoin" />
    <bpmn:sequenceFlow id="F_ToEval" sourceRef="Gateway_BreachJoin" targetRef="Gateway_ReportXOR" />
    <bpmn:sequenceFlow id="F_NotifyAll" name="Yes" sourceRef="Gateway_ReportXOR" targetRef="Task_NotifyRegulator" />
    <bpmn:sequenceFlow id="F_LogOnly" name="No" sourceRef="Gateway_ReportXOR" targetRef="Task_InternalRecord" />
    <bpmn:sequenceFlow id="F_DoneNotif" sourceRef="Task_NotifyRegulator" targetRef="End_BreachClosed" />
    <bpmn:sequenceFlow id="F_DoneLog" sourceRef="Task_InternalRecord" targetRef="End_BreachClosed" />
  </bpmn:process>
</bpmn:definitions>"""
    }
]

def ensure_corpus(corpus_dir: str):
    """Writes the 12 ground-truth .bpmn XML files and parses their canonical graphs."""
    os.makedirs(corpus_dir, exist_ok=True)
    models_dir = os.path.join(corpus_dir, "models")
    os.makedirs(models_dir, exist_ok=True)

    manifest = []
    for item in CORPUS_MODELS:
        xml_path = os.path.join(models_dir, f"{item['id']}.bpmn")
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(item["bpmn_xml"].strip())

        graph = BPMNParser.parse_xml_to_graph(item["bpmn_xml"], fallback_title=item["title"])
        manifest.append({
            "id": item["id"],
            "title": item["title"],
            "archetype": item["archetype"],
            "reference": item["reference"],
            "file_path": xml_path,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "lanes": graph.lanes
        })

    manifest_path = os.path.join(corpus_dir, "corpus_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return manifest
