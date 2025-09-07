use std::{collections::{HashMap, HashSet}, sync::Arc};
use once_cell::sync::Lazy;
use poem::{http::StatusCode, web::Data};
use poem_openapi::{payload::{Json, PlainText}, Object, OpenApi};
use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;
use uuid::Uuid;

// ---------------- Scopes ----------------
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Scope {
    AuthAdmin,  // manage keys
    VmCreate,
    VmRead,
    VmDelete,
    VmExec,
}

impl Scope {
    pub fn as_str(&self) -> &'static str {
        match self {
            Scope::AuthAdmin => "auth:admin",
            Scope::VmCreate  => "vm:create",
            Scope::VmRead    => "vm:read",
            Scope::VmDelete  => "vm:delete",
            Scope::VmExec    => "vm:exec",
        }
    }
}

// ---------------- Records + State ----------------
#[derive(Clone, Serialize, Deserialize)]
pub struct ApiKeyRecord {
    pub key: String,
    pub label: String,
    pub scopes: Vec<String>,
    pub allowed_fields: Option<Vec<String>>, // for masking helper
    pub created_at_ms: i64,
    pub disabled: bool,
}

#[derive(Clone)]
pub struct AuthState {
    inner: Arc<RwLock<HashMap<String, ApiKeyRecord>>>,
    admin_key: String,
}

impl AuthState {
    pub fn new(admin_key_from_env: Option<String>) -> Self {
        let admin = admin_key_from_env.unwrap_or_else(|| Uuid::new_v4().to_string());
        let s = Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
            admin_key: admin.clone(),
        };
        // Insert admin key with full scopes
        let rec = ApiKeyRecord {
            key: admin.clone(),
            label: "bootstrap-admin".into(),
            scopes: vec![
                Scope::AuthAdmin.as_str().into(),
                Scope::VmCreate.as_str().into(),
                Scope::VmRead.as_str().into(),
                Scope::VmDelete.as_str().into(),
                Scope::VmExec.as_str().into(),
            ],
            allowed_fields: None,
            created_at_ms: chrono::Utc::now().timestamp_millis(),
            disabled: false,
        };
        futures::executor::block_on(async {
            s.inner.write().await.insert(admin.clone(), rec);
        });
        s
    }

    /// Returns a line to print once at boot so you can grab the admin key.
    pub fn boot_msg(&self) -> Option<String> {
        Some(format!(
            "ADMIN API KEY: {}  (store it safely or set ADMIN_API_KEY to control it)",
            self.admin_key
        ))
    }

    pub async fn put(&self, rec: ApiKeyRecord) {
        self.inner.write().await.insert(rec.key.clone(), rec);
    }

    pub async fn get(&self, key: &str) -> Option<ApiKeyRecord> {
        self.inner.read().await.get(key).cloned()
    }

    pub async fn delete(&self, key: &str) -> bool {
        self.inner.write().await.remove(key).is_some()
    }

    pub fn require(&self, key_hdr: &Option<String>, scope: Scope) -> Result<ApiKeyRecord, String> {
        let key = key_hdr.as_ref().ok_or_else(|| "missing x-api-key".to_string())?;
        // Synchronously block only on the small read
        let rec_opt = futures::executor::block_on(async { self.get(key).await });
        let rec = rec_opt.ok_or_else(|| "invalid api key".to_string())?;
        if rec.disabled {
            return Err("api key disabled".into());
        }
        let needed = scope.as_str();
        if !rec.scopes.iter().any(|s| s == needed) {
            return Err(format!("missing scope: {}", needed));
        }
        Ok(rec)
    }
}

// ---------------- Masking helper ----------------

/// Keep only fields present in `allowed`. Works for top-level objects and recursively for nested objects
/// when `allowed` contains path-like entries such as "a", "a.b", "a.b.c".
pub fn mask_json(value: &serde_json::Value, allowed: &HashSet<String>) -> serde_json::Value {
    use serde_json::{Map, Value};
    fn allowed_prefixes(key: &str, allowed: &HashSet<String>) -> Vec<String> {
        allowed
            .iter()
            .filter(|a| a.starts_with(&format!("{key}")) && (a.len() == key.len() || a.as_bytes()[key.len()] == b'.'))
            .cloned()
            .collect()
    }
    match value {
        Value::Object(map) => {
            let mut out = Map::new();
            for (k, v) in map {
                // Allow direct key
                let direct = allowed.contains(k);
                // Allow nested paths like "user.name"
                let nested_vec = allowed_prefixes(k, allowed);
                if direct {
                    out.insert(k.clone(), v.clone());
                } else if !nested_vec.is_empty() {
                    // Build child allow-set stripped of the "<k>." prefix
                    let child_set: HashSet<String> = nested_vec
                        .into_iter()
                        .filter_map(|p| p.strip_prefix(&format!("{k}.")).map(|s| s.to_string()))
                        .collect();
                    out.insert(k.clone(), mask_json(v, &child_set));
                }
            }
            Value::Object(out)
        }
        Value::Array(arr) => {
            Value::Array(arr.iter().map(|v| mask_json(v, allowed)).collect())
        }
        other => other.clone(),
    }
}

// ---------------- OpenAPI for managing keys & masking ----------------

#[derive(Object)]
struct CreateKeyRequest {
    /// Optional human label
    label: Option<String>,
    /// Scopes for the key (e.g. ["vm:create","vm:read"])
    scopes: Vec<String>,
    /// Optional allowed fields to retain when using the mask endpoint (e.g. ["id","user.name"])
    allowed_fields: Option<Vec<String>>,
}

#[derive(Object)]
struct KeyInfo {
    key: String,
    label: String,
    scopes: Vec<String>,
    allowed_fields: Option<Vec<String>>,
    created_at_ms: i64,
    disabled: bool,
}

#[derive(Object)]
struct MaskRequest {
    data: serde_json::Value,
    allowed_fields: Option<Vec<String>>,
}

pub struct AuthApi;

#[OpenApi]
impl AuthApi {
    /// Create a new API key. Requires `auth:admin`. Send your admin key in `x-api-key`.
    #[oai(path = "/auth/keys", method = "post")]
    async fn create_key(
        &self,
        auth: Data<&AuthState>,
        api_key: poem_openapi::param::Header<Option<String>>,
        body: Json<CreateKeyRequest>,
    ) -> poem::Result<Json<KeyInfo>> {
        auth.require(&api_key.0, Scope::AuthAdmin)
            .map_err(|e| poem::Error::from_string(e, StatusCode::UNAUTHORIZED))?;

        let now = chrono::Utc::now().timestamp_millis();
        let key = Uuid::new_v4().to_string();
        let rec = ApiKeyRecord {
            key: key.clone(),
            label: body.label.clone().unwrap_or_else(|| "user-key".into()),
            scopes: body.scopes.clone(),
            allowed_fields: body.allowed_fields.clone(),
            created_at_ms: now,
            disabled: false,
        };
        auth.put(rec.clone()).await;

        Ok(Json(KeyInfo {
            key: rec.key,
            label: rec.label,
            scopes: rec.scopes,
            allowed_fields: rec.allowed_fields,
            created_at_ms: rec.created_at_ms,
            disabled: rec.disabled,
        }))
    }

    /// List keys. Requires `auth:admin`.
    #[oai(path = "/auth/keys", method = "get")]
    async fn list_keys(
        &self,
        auth: Data<&AuthState>,
        api_key: poem_openapi::param::Header<Option<String>>,
    ) -> poem::Result<Json<Vec<KeyInfo>>> {
        auth.require(&api_key.0, Scope::AuthAdmin)
            .map_err(|e| poem::Error::from_string(e, StatusCode::UNAUTHORIZED))?;

        let map = auth.inner.read().await;
        let mut v: Vec<KeyInfo> = Vec::new();
        for rec in map.values() {
            v.push(KeyInfo {
                key: rec.key.clone(),
                label: rec.label.clone(),
                scopes: rec.scopes.clone(),
                allowed_fields: rec.allowed_fields.clone(),
                created_at_ms: rec.created_at_ms,
                disabled: rec.disabled,
            });
        }
        Ok(Json(v))
    }

    /// Delete a key. Requires `auth:admin`.
    #[oai(path = "/auth/keys/:key", method = "delete")]
    async fn delete_key(
        &self,
        auth: Data<&AuthState>,
        api_key: poem_openapi::param::Header<Option<String>>,
        key: poem_openapi::param::Path<String>,
    ) -> poem::Result<PlainText<String>> {
        auth.require(&api_key.0, Scope::AuthAdmin)
            .map_err(|e| poem::Error::from_string(e, StatusCode::UNAUTHORIZED))?;

        let ok = auth.delete(&key.0).await;
        if ok {
            Ok(PlainText("deleted".into()))
        } else {
            Ok(PlainText("not found".into()))
        }
    }

    /// Mask JSON by allowed fields. If the caller's key has `allowed_fields`, those apply unless overridden.
    /// No scope required; you still need a valid key.
    #[oai(path = "/auth/mask", method = "post")]
    async fn mask(
        &self,
        auth: Data<&AuthState>,
        api_key: poem_openapi::param::Header<Option<String>>,
        body: Json<MaskRequest>,
    ) -> poem::Result<Json<serde_json::Value>> {
        let rec = auth
            .require(&api_key.0, Scope::VmRead) // pick a low bar scope for demonstration
            .or_else(|_| auth.require(&api_key.0, Scope::AuthAdmin))
            .map_err(|e| poem::Error::from_string(e, StatusCode::UNAUTHORIZED))?;

        let allowed = body
            .allowed_fields
            .clone()
            .or(rec.allowed_fields.clone())
            .unwrap_or_else(|| vec![]);

        let set: HashSet<String> = allowed.into_iter().collect();
        let masked = mask_json(&body.data, &set);
        Ok(Json(masked))
    }
}

// Simple adapter type to clarify require() return in other modules
pub type Authz = Result<ApiKeyRecord, String>;
