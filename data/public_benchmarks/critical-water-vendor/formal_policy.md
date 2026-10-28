# CRITICAL INFRASTRUCTURE MANDATE: REMOTE VENDOR ACCESS TO SCADA / OT
Governed by: NIS2 Directive / KRITIS Municipal Infrastructure Security Group

1. SUPPLIER SECURITY & JUST-IN-TIME SESSIONS (ISO 27002: A.5.19, A.5.20, A.8.2)
All external maintenance vendors connecting to municipal water pumping telemetry must submit a 24-hour advance maintenance notice. Access is granted solely through a monitored Jump Host with recorded sessions and time-boxed MFA tokens.

2. NETWORK SEGREGATION (ISO 27002: A.8.20, A.8.22)
The Operational Technology (OT / SCADA) network must be air-gapped or separated via industrial unidirectional gateways (data diodes) and Next-Gen Firewalls from the municipal administrative corporate network.

3. LOGGING & AUDIT TRAIL (ISO 27002: A.8.15, A.8.16)
All telemetry command executions and vendor terminal inputs must be forwarded in real-time to the Municipal Security Operations Center SIEM repository with 365-day tamper-proof retention.