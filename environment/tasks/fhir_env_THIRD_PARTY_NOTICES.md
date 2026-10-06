# Third-party sources

Synthetic Hospital v1.3: https://github.com/sparkcpark/synthetic_hospital, commit `911f34c4ac65a508543c4b3b90c373a0cd16534d`, Christine Park, Valerie Chen and Tim Dettmers. The source code is MIT. The released data card describes fully synthetic redistributable data and separately discusses terminology licensing. Its exact LICENSE is retained in the HF release preparation. The importer excludes the ontology tables.

FHIR R4 4.0.1 JSON schema: https://hl7.org/fhir/R4/fhir.schema.json.zip. Copyright HL7; FHIR specification licensing: https://hl7.org/fhir/R4/license.html. The schema file is distributed verbatim in this research package. The local validator checks JSON shape and selected graph constraints; it does not claim complete conformance.

LOINC example 718-7 (hemoglobin): https://loinc.org/718-7/. LOINC is copyright Regenstrief Institute, Inc. and the LOINC Committee; see https://loinc.org/license/. The benchmark uses this single standard example code and does not redistribute a terminology database.

FHIR concepts and local benchmark codes are used for simulation. Local codes have no asserted mapping to clinical terminology. The user's generated derivative currently has no newly assigned public license.
