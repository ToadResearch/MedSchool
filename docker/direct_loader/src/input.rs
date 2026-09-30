use anyhow::{bail, Context, Result};
use flate2::read::GzDecoder;
use rayon::prelude::*;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use url::Url;
use walkdir::WalkDir;

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct ResourceKey {
    pub resource_type: String,
    pub id: String,
}

impl ResourceKey {
    pub fn relative_reference(&self) -> String {
        format!("{}/{}", self.resource_type, self.id)
    }
}

#[derive(Debug)]
struct ExtractedResource {
    full_url: Option<String>,
    value: Value,
}

#[derive(Default)]
pub struct ReferenceIndex {
    exact: HashMap<String, ResourceKey>,
    identifiers: HashMap<IdentifierKey, Option<ResourceKey>>,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct IdentifierKey {
    resource_type: String,
    system: String,
    value: String,
}

#[derive(Debug)]
pub struct StagedResource {
    pub resource_type: String,
    pub id: String,
    pub body: String,
    pub hash_sha256: String,
}

#[derive(Default, Debug)]
pub struct LoadStats {
    pub files: usize,
    pub resources: usize,
    pub duplicates: usize,
    pub references_rewritten: u64,
}

pub fn discover_files(root: &Path) -> Result<Vec<PathBuf>> {
    if !root.is_dir() {
        bail!("input directory does not exist: {}", root.display());
    }

    let mut paths = Vec::new();
    for entry in WalkDir::new(root).follow_links(false) {
        let entry = entry.with_context(|| format!("walking input directory {}", root.display()))?;
        if entry.file_type().is_file() {
            let path = entry.into_path();
            let name = path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or_default()
                .to_ascii_lowercase();
            if name.ends_with(".json") || name.ends_with(".json.gz") || name.ends_with(".ndjson") {
                paths.push(path);
            }
        }
    }
    paths.sort();

    if paths.is_empty() {
        bail!(
            "no .json, .json.gz, or .ndjson files found below {}",
            root.display()
        );
    }
    Ok(paths)
}

pub fn build_reference_index(paths: &[PathBuf], chunk_size: usize) -> Result<ReferenceIndex> {
    let mut index = ReferenceIndex::default();
    for chunk in paths.chunks(chunk_size.max(1)) {
        // Bound the first pass just like the write pass: only one chunk of parsed resource
        // bodies is resident at once. The accumulated index contains only compact keys.
        let partials = chunk
            .par_iter()
            .map(|path| scan_file(path))
            .collect::<Result<Vec<_>>>()?;

        for partial in partials {
            for item in partial {
                let Some(key) = resource_key(&item.value)? else {
                    continue;
                };

                insert_exact(&mut index.exact, key.relative_reference(), &key)?;
                insert_exact(&mut index.exact, format!("urn:uuid:{}", key.id), &key)?;
                if let Some(full_url) = item.full_url {
                    insert_exact(&mut index.exact, full_url, &key)?;
                }

                if let Some(identifiers) = item.value.get("identifier").and_then(Value::as_array) {
                    for identifier in identifiers {
                        let Some(value) = identifier.get("value").and_then(Value::as_str) else {
                            continue;
                        };
                        let system = identifier
                            .get("system")
                            .and_then(Value::as_str)
                            .unwrap_or_default();
                        insert_identifier(&mut index.identifiers, &key, system, value);
                        if !system.is_empty() {
                            // A conditional `identifier=value` search ignores the system. Keep a
                            // value-only key, but mark it ambiguous if more than one resource owns it.
                            insert_identifier(&mut index.identifiers, &key, "", value);
                        }
                    }
                }
            }
        }
    }
    Ok(index)
}

fn insert_identifier(
    map: &mut HashMap<IdentifierKey, Option<ResourceKey>>,
    resource: &ResourceKey,
    system: &str,
    value: &str,
) {
    let identifier = IdentifierKey {
        resource_type: resource.resource_type.clone(),
        system: system.to_owned(),
        value: value.to_owned(),
    };
    match map.get(&identifier) {
        None => {
            map.insert(identifier, Some(resource.clone()));
        }
        Some(Some(existing)) if existing != resource => {
            map.insert(identifier, None);
        }
        _ => {}
    }
}

fn insert_exact(
    map: &mut HashMap<String, ResourceKey>,
    reference: String,
    key: &ResourceKey,
) -> Result<()> {
    if let Some(existing) = map.get(&reference) {
        if existing != key {
            bail!(
                "reference {reference:?} points to both {} and {}",
                existing.relative_reference(),
                key.relative_reference()
            );
        }
    } else {
        map.insert(reference, key.clone());
    }
    Ok(())
}

pub fn visit_staged_resources<F>(
    paths: &[PathBuf],
    index: &ReferenceIndex,
    chunk_size: usize,
    mut visitor: F,
) -> Result<LoadStats>
where
    F: FnMut(StagedResource) -> Result<()>,
{
    let rewritten = AtomicU64::new(0);
    let mut seen = HashSet::<ResourceKey>::new();
    let mut stats = LoadStats {
        files: paths.len(),
        ..LoadStats::default()
    };

    for chunk in paths.chunks(chunk_size.max(1)) {
        let parsed = chunk
            .par_iter()
            .map(|path| {
                let mut output = Vec::new();
                for mut item in scan_file(path)? {
                    let Some(key) = resource_key(&item.value)? else {
                        continue;
                    };
                    rewrite_references(&mut item.value, index, &rewritten).with_context(|| {
                        format!(
                            "resolving references in {} from {}",
                            key.relative_reference(),
                            path.display()
                        )
                    })?;
                    let body = serde_json::to_string(&item.value)
                        .context("serializing normalized FHIR resource")?;
                    output.push((key, body));
                }
                Ok::<_, anyhow::Error>(output)
            })
            .collect::<Result<Vec<_>>>()?;

        for file_resources in parsed {
            for (key, body) in file_resources {
                if !seen.insert(key.clone()) {
                    stats.duplicates += 1;
                    continue;
                }
                visitor(StagedResource {
                    resource_type: key.resource_type,
                    id: key.id,
                    hash_sha256: hapi_hash_unencoded_chars(&body),
                    body,
                })?;
                stats.resources += 1;
            }
        }
    }

    stats.references_rewritten = rewritten.load(Ordering::Relaxed);
    Ok(stats)
}

fn scan_file(path: &Path) -> Result<Vec<ExtractedResource>> {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();

    if name.ends_with(".ndjson") {
        let reader = BufReader::new(
            File::open(path).with_context(|| format!("opening {}", path.display()))?,
        );
        let mut output = Vec::new();
        for (line_number, line) in reader.lines().enumerate() {
            let line = line.with_context(|| format!("reading {}", path.display()))?;
            if line.trim().is_empty() {
                continue;
            }
            let value: Value = serde_json::from_str(&line)
                .with_context(|| format!("parsing {} line {}", path.display(), line_number + 1))?;
            extract_value(value, &mut output);
        }
        return Ok(output);
    }

    let file = File::open(path).with_context(|| format!("opening {}", path.display()))?;
    let mut reader: Box<dyn Read> = if name.ends_with(".gz") {
        Box::new(GzDecoder::new(file))
    } else {
        Box::new(file)
    };
    let mut bytes = Vec::new();
    reader
        .read_to_end(&mut bytes)
        .with_context(|| format!("reading {}", path.display()))?;
    let value: Value =
        serde_json::from_slice(&bytes).with_context(|| format!("parsing {}", path.display()))?;
    let mut output = Vec::new();
    extract_value(value, &mut output);
    Ok(output)
}

fn extract_value(value: Value, output: &mut Vec<ExtractedResource>) {
    if value.get("resourceType").and_then(Value::as_str) == Some("Bundle") {
        if let Some(entries) = value.get("entry").and_then(Value::as_array) {
            for entry in entries {
                let Some(resource) = entry.get("resource") else {
                    continue;
                };
                output.push(ExtractedResource {
                    full_url: entry
                        .get("fullUrl")
                        .and_then(Value::as_str)
                        .map(str::to_owned),
                    value: resource.clone(),
                });
            }
        }
    } else if value.get("resourceType").and_then(Value::as_str).is_some() {
        output.push(ExtractedResource {
            full_url: None,
            value,
        });
    }
    // Synthea's timestamp-prefixed run metadata files intentionally land here and are ignored.
}

fn resource_key(value: &Value) -> Result<Option<ResourceKey>> {
    let Some(resource_type) = value.get("resourceType").and_then(Value::as_str) else {
        return Ok(None);
    };
    let Some(id) = value.get("id").and_then(Value::as_str) else {
        bail!("{resource_type} resource has no id; direct loading requires stable logical IDs");
    };
    if !valid_resource_type(resource_type) {
        bail!("invalid FHIR resourceType {resource_type:?}");
    }
    if !valid_fhir_id(id) {
        bail!("invalid FHIR id {id:?} on {resource_type}");
    }
    Ok(Some(ResourceKey {
        resource_type: resource_type.to_owned(),
        id: id.to_owned(),
    }))
}

fn valid_resource_type(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 40
        && value.bytes().all(|byte| byte.is_ascii_alphanumeric())
        && value.as_bytes()[0].is_ascii_uppercase()
}

fn valid_fhir_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'.')
}

fn rewrite_references(
    value: &mut Value,
    index: &ReferenceIndex,
    rewritten: &AtomicU64,
) -> Result<()> {
    match value {
        Value::Array(values) => {
            for value in values {
                rewrite_references(value, index, rewritten)?;
            }
        }
        Value::Object(object) => {
            if let Some(reference_value) = object.get_mut("reference") {
                if let Some(reference) = reference_value.as_str() {
                    if let Some(replacement) = resolve_reference(reference, index)? {
                        if replacement != reference {
                            *reference_value = Value::String(replacement);
                            rewritten.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                }
            }
            for (key, child) in object.iter_mut() {
                if key != "reference" {
                    rewrite_references(child, index, rewritten)?;
                }
            }
        }
        _ => {}
    }
    Ok(())
}

fn resolve_reference(reference: &str, index: &ReferenceIndex) -> Result<Option<String>> {
    if reference.starts_with('#') {
        return Ok(None);
    }
    if let Some(key) = index.exact.get(reference) {
        return Ok(Some(key.relative_reference()));
    }

    if reference.starts_with("urn:uuid:") {
        bail!("unresolved local UUID reference {reference:?}");
    }

    let target = reference.split('?').next().unwrap_or(reference);
    let parsed = if target.contains("://") {
        Url::parse(reference).ok()
    } else {
        Url::parse(&format!("http://synthea.invalid/{reference}")).ok()
    };
    let Some(parsed) = parsed else {
        return Ok(None);
    };

    let resource_type = parsed
        .path_segments()
        .and_then(|mut segments| segments.next_back())
        .unwrap_or_default();
    let identifier = parsed
        .query_pairs()
        .find(|(name, _)| name == "identifier")
        .map(|(_, value)| value.into_owned());
    let Some(identifier) = identifier else {
        return Ok(None);
    };

    let (system, value) = identifier.split_once('|').unwrap_or(("", &identifier));
    let lookup = IdentifierKey {
        resource_type: resource_type.to_owned(),
        system: system.to_owned(),
        value: value.to_owned(),
    };
    match index.identifiers.get(&lookup) {
        Some(Some(key)) => Ok(Some(key.relative_reference())),
        Some(None) => bail!("ambiguous conditional reference {reference:?}"),
        None => bail!("unresolved conditional reference {reference:?}"),
    }
}

/// HAPI 8.4 uses Guava's `Hashing.sha256().hashUnencodedChars`, which hashes
/// Java UTF-16 code units in little-endian order rather than UTF-8 bytes.
fn hapi_hash_unencoded_chars(value: &str) -> String {
    let mut hasher = Sha256::new();
    for code_unit in value.encode_utf16() {
        hasher.update(code_unit.to_le_bytes());
    }
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::AtomicU64;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn rewrites_uuid_and_conditional_references() {
        let patient = ResourceKey {
            resource_type: "Patient".into(),
            id: "p1".into(),
        };
        let practitioner = ResourceKey {
            resource_type: "Practitioner".into(),
            id: "pr1".into(),
        };
        let mut index = ReferenceIndex::default();
        index.exact.insert("urn:uuid:patient".into(), patient);
        index.identifiers.insert(
            IdentifierKey {
                resource_type: "Practitioner".into(),
                system: "http://example.org/npi".into(),
                value: "abc".into(),
            },
            Some(practitioner),
        );
        let mut resource = serde_json::json!({
            "subject": {"reference": "urn:uuid:patient"},
            "performer": [{"reference": "Practitioner?identifier=http://example.org/npi%7Cabc"}]
        });

        rewrite_references(&mut resource, &index, &AtomicU64::new(0)).unwrap();

        assert_eq!(resource["subject"]["reference"], "Patient/p1");
        assert_eq!(resource["performer"][0]["reference"], "Practitioner/pr1");
    }

    #[test]
    fn rejects_unresolved_local_reference() {
        let mut resource = serde_json::json!({"subject": {"reference": "urn:uuid:missing"}});
        let error = rewrite_references(
            &mut resource,
            &ReferenceIndex::default(),
            &AtomicU64::new(0),
        )
        .unwrap_err();
        assert!(error.to_string().contains("unresolved local UUID"));
    }

    #[test]
    fn validates_logical_ids() {
        assert!(valid_fhir_id("550e8400-e29b-41d4-a716-446655440000"));
        assert!(!valid_fhir_id("has/a/slash"));
        assert!(!valid_fhir_id(""));
    }

    #[test]
    fn hash_is_stable() {
        assert_eq!(
            hapi_hash_unencoded_chars("abc"),
            "13e228567e8249fce53337f25d7970de3bd68ab2653424c7b8f9fd05e33caedf"
        );
    }

    #[test]
    fn scans_and_normalizes_a_synthea_bundle_end_to_end() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "synthea-fhir-db-loader-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir(&directory).unwrap();
        let bundle_path = directory.join("patient.json");
        fs::write(
            &bundle_path,
            serde_json::to_vec(&serde_json::json!({
                "resourceType": "Bundle",
                "type": "transaction",
                "entry": [
                    {
                        "fullUrl": "urn:uuid:practitioner",
                        "resource": {
                            "resourceType": "Practitioner",
                            "id": "pr1",
                            "identifier": [{
                                "system": "http://hl7.org/fhir/sid/us-npi",
                                "value": "123"
                            }]
                        }
                    },
                    {
                        "fullUrl": "urn:uuid:patient",
                        "resource": {"resourceType": "Patient", "id": "p1"}
                    },
                    {
                        "fullUrl": "urn:uuid:observation",
                        "resource": {
                            "resourceType": "Observation",
                            "id": "o1",
                            "subject": {"reference": "urn:uuid:patient"},
                            "performer": [{
                                "reference": "Practitioner?identifier=http://hl7.org/fhir/sid/us-npi%7C123"
                            }]
                        }
                    }
                ]
            }))
            .unwrap(),
        )
        .unwrap();

        let paths = discover_files(&directory).unwrap();
        let index = build_reference_index(&paths, 1).unwrap();
        let mut staged = Vec::new();
        let stats = visit_staged_resources(&paths, &index, 1, |resource| {
            staged.push(resource);
            Ok(())
        })
        .unwrap();

        assert_eq!(stats.resources, 3);
        assert_eq!(stats.references_rewritten, 2);
        let observation = staged
            .iter()
            .find(|resource| resource.resource_type == "Observation")
            .unwrap();
        assert!(observation.body.contains(r#""reference":"Patient/p1""#));
        assert!(observation
            .body
            .contains(r#""reference":"Practitioner/pr1""#));

        fs::remove_file(bundle_path).unwrap();
        fs::remove_dir(directory).unwrap();
    }
}
