use crate::input::StagedResource;
use anyhow::{bail, Context, Result};
use postgres::{Client, Config, NoTls, Transaction};
use std::env;
use std::io::Write;

const LOADER_LOCK_ID: i64 = 0x4d_45_44_53_43_48_4f_4f;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ImportMode {
    Initialize,
    Append,
}

#[derive(Debug)]
pub struct InsertSummary {
    pub staged: i64,
    pub inserted: i64,
    pub skipped_existing: i64,
    pub resource_types: Vec<String>,
}

pub fn connect_from_env() -> Result<Client> {
    let mut config = Config::new();
    config
        .host(&env::var("PGHOST").unwrap_or_else(|_| "db".into()))
        .port(
            env::var("PGPORT")
                .unwrap_or_else(|_| "5432".into())
                .parse()
                .context("PGPORT must be a valid port number")?,
        )
        .user(&env::var("PGUSER").context("PGUSER is required")?)
        .dbname(&env::var("PGDATABASE").unwrap_or_else(|_| "hapi".into()));
    if let Ok(password) = env::var("PGPASSWORD") {
        config.password(password);
    }
    config
        .connect(NoTls)
        .context("connecting to the HAPI PostgreSQL database")
}

pub struct LoaderTransaction<'a> {
    transaction: Transaction<'a>,
}

impl<'a> LoaderTransaction<'a> {
    pub fn begin(client: &'a mut Client, mode: ImportMode) -> Result<Self> {
        let mut transaction = client
            .transaction()
            .context("starting loader transaction")?;
        transaction
            .query_one("SELECT pg_advisory_xact_lock($1)", &[&LOADER_LOCK_ID])
            .context("acquiring the direct-loader advisory lock")?;
        validate_schema(&mut transaction)?;

        let existing: i64 = transaction
            .query_one(
                "SELECT count(*)::bigint FROM hfj_resource WHERE res_deleted_at IS NULL",
                &[],
            )?
            .get(0);
        if mode == ImportMode::Initialize && existing != 0 {
            bail!(
                "initialize mode requires an empty HAPI repository, but found {existing} resources; use --mode append to add only missing logical IDs"
            );
        }

        transaction.batch_execute(
            r#"
            CREATE TEMP TABLE fhir_direct_stage (
                ordinal bigint NOT NULL,
                res_type varchar(40) NOT NULL,
                fhir_id varchar(64) NOT NULL,
                res_body text NOT NULL,
                hash_sha256 varchar(64) NOT NULL,
                res_id bigint,
                res_ver_pid bigint,
                res_type_id smallint,
                PRIMARY KEY (res_type, fhir_id)
            ) ON COMMIT DROP
            "#,
        )?;

        Ok(Self { transaction })
    }

    pub fn copy_with<F>(&mut self, load: F) -> Result<(u64, crate::input::LoadStats)>
    where
        F: FnOnce(&mut dyn FnMut(StagedResource) -> Result<()>) -> Result<crate::input::LoadStats>,
    {
        let sink = self.transaction.copy_in(
            "COPY fhir_direct_stage (ordinal, res_type, fhir_id, res_body, hash_sha256) FROM STDIN WITH (FORMAT text)",
        )?;
        let mut writer = sink;
        let mut count = 0_u64;
        let mut visitor = |resource: StagedResource| -> Result<()> {
            count += 1;
            writeln!(
                writer,
                "{}\t{}\t{}\t{}\t{}",
                count,
                copy_escape(&resource.resource_type),
                copy_escape(&resource.id),
                copy_escape(&resource.body),
                resource.hash_sha256
            )
            .context("streaming a resource to PostgreSQL COPY")?;
            Ok(())
        };
        let stats = load(&mut visitor)?;
        writer.finish().context("finishing PostgreSQL COPY")?;
        Ok((count, stats))
    }

    pub fn require_matching_patients(&mut self) -> Result<()> {
        let incompatible: i64 = self
            .transaction
            .query_one(
                r#"SELECT count(*)::bigint FROM hfj_resource r
               WHERE r.res_type = 'Patient' AND r.res_deleted_at IS NULL
               AND NOT EXISTS (SELECT 1 FROM fhir_direct_stage s
                               WHERE s.res_type = 'Patient' AND s.fhir_id = r.fhir_id)"#,
                &[],
            )?
            .get(0);
        if incompatible != 0 {
            bail!("found {incompatible} existing patients outside the selected seed cohort; use a fresh database for replacement (back up existing data before an explicit --reset --data)");
        }
        Ok(())
    }

    pub fn insert(mut self) -> Result<InsertSummary> {
        let staged: i64 = self
            .transaction
            .query_one("SELECT count(*)::bigint FROM fhir_direct_stage", &[])?
            .get(0);

        let resource_types = self
            .transaction
            .query(
                "SELECT DISTINCT res_type FROM fhir_direct_stage ORDER BY res_type",
                &[],
            )?
            .into_iter()
            .map(|row| row.get::<_, String>(0))
            .collect();

        // HAPI 8.4 introduced this lookup table. allocationSize=1, so direct nextval is safe.
        self.transaction.batch_execute(
            r#"
            INSERT INTO hfj_resource_type (res_type_id, res_type)
            SELECT nextval('seq_resource_type')::smallint, missing.res_type
            FROM (
                SELECT DISTINCT s.res_type
                FROM fhir_direct_stage s
                LEFT JOIN hfj_resource_type rt ON rt.res_type = s.res_type
                WHERE rt.res_type_id IS NULL
                ORDER BY s.res_type
            ) missing
            ON CONFLICT (res_type) DO NOTHING;

            UPDATE fhir_direct_stage s
            SET res_type_id = rt.res_type_id
            FROM hfj_resource_type rt
            WHERE rt.res_type = s.res_type;

            -- Existing logical IDs are intentionally skipped in append mode. This loader never
            -- fabricates a HAPI history version or deletes live indexes behind the server's back.
            DELETE FROM fhir_direct_stage s
            USING hfj_resource r
            WHERE r.res_type = s.res_type
              AND r.fhir_id = s.fhir_id
              AND r.partition_id IS NULL;

            -- HAPI's sequence generator uses a pooled allocation size of 50. Calling nextval here
            -- consumes pool boundary values, leaving all later HAPI-allocated ranges disjoint.
            UPDATE fhir_direct_stage
            SET res_id = nextval('seq_resource_id'),
                res_ver_pid = nextval('seq_resource_history_id');

            INSERT INTO hfj_resource (
                res_id, partition_id, partition_date, res_deleted_at, res_version,
                has_tags, res_published, res_updated, fhir_id, sp_has_links,
                hash_sha256, sp_index_status, res_language, sp_cmpstr_uniq_present,
                sp_cmptoks_present, sp_coords_present, sp_date_present,
                sp_number_present, sp_quantity_nrml_present, sp_quantity_present,
                sp_string_present, sp_token_present, sp_uri_present,
                search_url_present, res_type, res_ver, res_type_id
            )
            SELECT
                res_id, NULL, NULL, NULL, 'R4',
                false, transaction_timestamp(), transaction_timestamp(), fhir_id, false,
                hash_sha256, NULL, NULL, false,
                false, false, false,
                false, false, false,
                false, false, false,
                false, res_type, 1, res_type_id
            FROM fhir_direct_stage
            ORDER BY ordinal;

            INSERT INTO hfj_res_ver (
                pid, partition_date, partition_id, res_deleted_at, res_version,
                has_tags, res_published, res_updated, res_encoding, request_id,
                res_text, res_id, res_text_vc, res_type, res_ver, source_uri,
                res_type_id
            )
            SELECT
                res_ver_pid, NULL, NULL, NULL, 'R4',
                false, transaction_timestamp(), transaction_timestamp(), 'JSON', NULL,
                NULL, res_id, res_body, res_type, 1, NULL,
                res_type_id
            FROM fhir_direct_stage
            ORDER BY ordinal;
            "#,
        )?;

        let inserted: i64 = self
            .transaction
            .query_one("SELECT count(*)::bigint FROM fhir_direct_stage", &[])?
            .get(0);

        self.transaction
            .commit()
            .context("committing direct FHIR load")?;
        Ok(InsertSummary {
            staged,
            inserted,
            skipped_existing: staged - inserted,
            resource_types,
        })
    }
}

fn validate_schema(transaction: &mut Transaction<'_>) -> Result<()> {
    let required = [
        ("hfj_resource", "res_type_id"),
        ("hfj_resource", "fhir_id"),
        ("hfj_res_ver", "res_text_vc"),
        ("hfj_res_ver", "res_type_id"),
        ("hfj_resource_type", "res_type_id"),
        ("bt2_job_instance", "stat"),
    ];
    for (table, column) in required {
        let exists: bool = transaction
            .query_one(
                r#"
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = $1
                      AND column_name = $2
                )
                "#,
                &[&table, &column],
            )?
            .get(0);
        if !exists {
            bail!(
                "database is not a compatible HAPI FHIR JPA 8.4 schema: missing {table}.{column}"
            );
        }
    }

    for sequence in [
        "seq_resource_id",
        "seq_resource_history_id",
        "seq_resource_type",
    ] {
        let exists: bool = transaction
            .query_one("SELECT to_regclass($1) IS NOT NULL", &[&sequence])?
            .get(0);
        if !exists {
            bail!("database is missing required HAPI sequence {sequence}");
        }
    }
    Ok(())
}

pub fn read_reindex_status(client: &mut Client, job_id: &str) -> Result<Option<ReindexStatus>> {
    let row = client.query_opt(
        r#"
        SELECT stat, progress_pct, error_count, error_msg, cmb_recs_processed
        FROM bt2_job_instance
        WHERE id = $1
        "#,
        &[&job_id],
    )?;
    Ok(row.map(|row| ReindexStatus {
        state: row.get(0),
        progress: row.get::<_, Option<f64>>(1).unwrap_or_default(),
        error_count: row.get::<_, Option<i32>>(2).unwrap_or_default(),
        error_message: row.get(3),
        records_processed: row.get::<_, Option<i32>>(4).unwrap_or_default(),
    }))
}

#[derive(Debug)]
pub struct ReindexStatus {
    pub state: String,
    pub progress: f64,
    pub error_count: i32,
    pub error_message: Option<String>,
    pub records_processed: i32,
}

fn copy_escape(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for character in value.chars() {
        match character {
            '\\' => output.push_str("\\\\"),
            '\t' => output.push_str("\\t"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            _ => output.push(character),
        }
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_postgres_copy_text() {
        assert_eq!(copy_escape("a\\b\tc\nd\r"), "a\\\\b\\tc\\nd\\r");
    }
}
