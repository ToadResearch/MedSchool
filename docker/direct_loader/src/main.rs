mod db;
mod input;
mod reindex;

use anyhow::{bail, Context, Result};
use clap::{Parser, ValueEnum};
use db::{ImportMode, LoaderTransaction};
use std::path::PathBuf;
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug, ValueEnum)]
enum ModeArg {
    Initialize,
    Append,
}

impl From<ModeArg> for ImportMode {
    fn from(value: ModeArg) -> Self {
        match value {
            ModeArg::Initialize => ImportMode::Initialize,
            ModeArg::Append => ImportMode::Append,
        }
    }
}

#[derive(Debug, Parser)]
#[command(version, about)]
struct Args {
    /// Directory containing Synthea FHIR JSON bundles or NDJSON files.
    #[arg(long, env = "INPUT_DIR", default_value = "/synthea")]
    input: PathBuf,

    /// Refuse a non-empty repository (initialize) or add only missing Type/id pairs (append).
    #[arg(long, env = "IMPORT_MODE", value_enum, default_value = "initialize")]
    mode: ModeArg,

    /// HAPI R4 base URL used only to start its local search reindexer after the direct write.
    #[arg(long, env = "FHIR_BASE_URL", default_value = "http://hapi:8080/fhir")]
    fhir_base_url: String,

    /// Leave search indexes empty. Direct reads work, but parameter searches will be incomplete.
    #[arg(long, env = "SKIP_REINDEX", default_value_t = false)]
    skip_reindex: bool,

    /// Maximum time to wait for HAPI's local reindex job.
    #[arg(long, env = "REINDEX_TIMEOUT_SECONDS", default_value_t = 3600)]
    reindex_timeout_seconds: u64,

    /// Number of input files parsed in parallel before streaming a batch to PostgreSQL.
    #[arg(long, env = "PARSE_CHUNK_SIZE", default_value_t = 32)]
    parse_chunk_size: usize,

    /// Parse and resolve everything without connecting to PostgreSQL.
    #[arg(long, default_value_t = false)]
    dry_run: bool,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("fatal: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let args = Args::parse();
    if args.parse_chunk_size == 0 {
        bail!("--parse-chunk-size must be at least 1");
    }

    let started = Instant::now();
    let paths = input::discover_files(&args.input)?;
    println!(
        "input_discovered root={} files={}",
        args.input.display(),
        paths.len()
    );

    let index = input::build_reference_index(&paths, args.parse_chunk_size)?;
    println!("reference_index_ready");

    if args.dry_run {
        let stats =
            input::visit_staged_resources(&paths, &index, args.parse_chunk_size, |_| Ok(()))?;
        println!(
            "dry_run_complete files={} resources={} duplicates={} references_rewritten={} elapsed_seconds={:.2}",
            stats.files,
            stats.resources,
            stats.duplicates,
            stats.references_rewritten,
            started.elapsed().as_secs_f64()
        );
        return Ok(());
    }

    let mut database = db::connect_from_env()?;
    let mut loader = LoaderTransaction::begin(&mut database, args.mode.into())?;
    let (copied, stats) = loader.copy_with(|visitor| {
        input::visit_staged_resources(&paths, &index, args.parse_chunk_size, visitor)
    })?;
    if copied == 0 {
        bail!("input files contained no FHIR resources with stable logical IDs");
    }
    let summary = loader.insert()?;
    println!(
        "database_load_complete files={} resources={} staged={} copied={} duplicates={} references_rewritten={} inserted={} skipped_existing={} elapsed_seconds={:.2}",
        stats.files,
        stats.resources,
        summary.staged,
        copied,
        stats.duplicates,
        stats.references_rewritten,
        summary.inserted,
        summary.skipped_existing,
        started.elapsed().as_secs_f64()
    );

    if summary.inserted == 0 {
        println!("reindex_skipped reason=no_new_resources");
        return Ok(());
    }
    if args.skip_reindex {
        eprintln!(
            "warning: reindex skipped; direct Type/id reads work, but FHIR parameter searches are incomplete"
        );
        return Ok(());
    }

    reindex::start_and_wait(
        &mut database,
        &args.fhir_base_url,
        &summary.resource_types,
        Duration::from_secs(args.reindex_timeout_seconds),
    )
    .context("reindexing directly loaded resources")?;
    println!(
        "load_complete inserted={} elapsed_seconds={:.2}",
        summary.inserted,
        started.elapsed().as_secs_f64()
    );
    Ok(())
}
