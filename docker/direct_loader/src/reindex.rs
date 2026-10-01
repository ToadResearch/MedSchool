use crate::db::read_reindex_status;
use anyhow::{bail, Context, Result};
use postgres::Client;
use reqwest::blocking::Client as HttpClient;
use serde_json::{json, Value};
use std::thread;
use std::time::{Duration, Instant};

pub fn start_and_wait(
    database: &mut Client,
    fhir_base_url: &str,
    resource_types: &[String],
    timeout: Duration,
) -> Result<()> {
    let parameters = resource_types
        .iter()
        .map(|resource_type| json!({"name": "url", "valueString": format!("{resource_type}?")}))
        .collect::<Vec<_>>();
    let url = format!("{}/$reindex", fhir_base_url.trim_end_matches('/'));
    let response = HttpClient::builder()
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(60))
        .build()?
        .post(&url)
        .header("Content-Type", "application/fhir+json")
        .header("Accept", "application/fhir+json")
        .json(&json!({"resourceType": "Parameters", "parameter": parameters}))
        .send()
        .with_context(|| format!("starting HAPI reindex at {url}"))?;

    let status = response.status();
    let body: Value = response
        .json()
        .context("decoding HAPI reindex response as JSON")?;
    if !status.is_success() {
        bail!("HAPI rejected $reindex with HTTP {status}: {body}");
    }
    let job_id = body
        .get("parameter")
        .and_then(Value::as_array)
        .and_then(|parameters| {
            parameters.iter().find_map(|parameter| {
                (parameter.get("name").and_then(Value::as_str) == Some("jobId"))
                    .then(|| parameter.get("valueString").and_then(Value::as_str))
                    .flatten()
            })
        })
        .context("HAPI $reindex response did not contain parameter jobId")?;

    println!("reindex_started job_id={job_id}");
    let started = Instant::now();
    let mut last_reported = String::new();
    loop {
        if started.elapsed() > timeout {
            bail!(
                "HAPI reindex job {job_id} did not finish within {} seconds",
                timeout.as_secs()
            );
        }
        if let Some(job) = read_reindex_status(database, job_id)? {
            let summary = format!(
                "{}:{:.1}:{}:{}",
                job.state,
                job.progress * 100.0,
                job.records_processed,
                job.error_count
            );
            if summary != last_reported {
                println!(
                    "reindex_progress job_id={} state={} progress_pct={:.1} records={} errors={}",
                    job_id,
                    job.state,
                    job.progress * 100.0,
                    job.records_processed,
                    job.error_count
                );
                last_reported = summary;
            }
            match job.state.as_str() {
                "COMPLETED" => return Ok(()),
                "FAILED" | "CANCELLED" => bail!(
                    "HAPI reindex job {job_id} ended as {}: {}",
                    job.state,
                    job.error_message
                        .unwrap_or_else(|| "no error message".into())
                ),
                _ => {}
            }
        }
        thread::sleep(Duration::from_secs(2));
    }
}
