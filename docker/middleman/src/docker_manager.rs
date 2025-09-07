use std::{collections::HashMap, sync::Arc, time::Duration};

use bollard::{
    exec::{CreateExecOptions, StartExecResults},
    models::{ContainerCreateBody, EndpointSettings, HostConfig},
    query_parameters::{
        CreateContainerOptions, CreateImageOptions, InspectContainerOptions,
        InspectNetworkOptions, ListContainersOptions, RemoveContainerOptions, StartContainerOptions,
        StopContainerOptions,
    },
    Docker,
};
use futures_util::TryStreamExt;
use poem::{http::StatusCode, web::Data};
use poem_openapi::{
    payload::{Json, PlainText},
    ApiResponse, Object, OpenApi,
};
use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;
use uuid::Uuid;

use crate::auth::{AuthState, Authz, Scope};

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

pub async fn resolve_network_name(docker: &Docker, desired: &str) -> Result<String, String> {
    if docker
        .inspect_network(desired, None::<InspectNetworkOptions>)
        .await
        .is_ok()
    {
        return Ok(desired.to_string());
    }

    // Fallback: the network of the running container (HOSTNAME = container id in Docker)
    let self_id = std::env::var("HOSTNAME").unwrap_or_default();
    let me = docker
        .inspect_container(&self_id, None::<InspectContainerOptions>)
        .await
        .map_err(|e| format!("inspect self failed: {e}"))?;

    if let Some(netmap) = me.network_settings.and_then(|ns| ns.networks) {
        if let Some((name, _)) = netmap.into_iter().next() {
            return Ok(name);
        }
    }
    Err(format!(
        "network '{desired}' not found, and could not infer a fallback from self"
    ))
}

// ------------------------ Types ------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub id: Uuid,
    pub container_id: String,
    pub image: String,
    pub created_at_ms: i64,
}

#[derive(Clone)]
pub struct AppState {
    pub docker: Docker,
    pub registry: Arc<RwLock<HashMap<Uuid, Session>>>,
    pub network: String,
    pub image: String,
    pub default_mem_mb: u64,
    pub default_cpus: f64,
}

impl AppState {
    pub async fn ensure_image(&self) -> Result<(), String> {
        let options = Some(CreateImageOptions {
            from_image: Some(self.image.clone()),
            ..Default::default()
        });
        let mut stream = self.docker.create_image(options, None, None);
        while let Some(_chunk) = stream.try_next().await.map_err(|e| e.to_string())? {
            // Swallow logs
        }
        Ok(())
    }

    fn labels_for(&self, session_id: Uuid) -> HashMap<String, String> {
        let mut m = HashMap::new();
        m.insert("mm.group".into(), "alpine-sessions".into());
        m.insert("mm.role".into(), "llm-sandbox".into());
        m.insert("mm.session_id".into(), session_id.to_string());
        m
    }

    pub async fn rebuild_registry_from_docker(&self) -> Result<(), String> {
        let mut filters = HashMap::<String, Vec<String>>::new();
        filters.insert("label".into(), vec!["mm.group=alpine-sessions".into()]);
        let opts = Some(ListContainersOptions {
            all: true,
            filters: Some(filters),
            ..Default::default()
        });

        let containers = self
            .docker
            .list_containers(opts)
            .await
            .map_err(|e| e.to_string())?;

        let mut map = self.registry.write().await;
        map.clear();

        for c in containers {
            if let Some(labels) = c.labels {
                if let Some(sid) = labels.get("mm.session_id") {
                    if let Ok(id) = Uuid::parse_str(sid) {
                        let image = c.image.clone().unwrap_or_else(|| self.image.clone());
                        let container_id = c.id.clone().unwrap_or_default();
                        let created_at_ms = c.created.unwrap_or_default() * 1000;
                        map.insert(
                            id,
                            Session {
                                id,
                                container_id,
                                image,
                                created_at_ms,
                            },
                        );
                    }
                }
            }
        }
        Ok(())
    }
}

// ---------- OpenAPI DTOs ----------

#[derive(Object)]
struct CreateSessionRequest {
    env: Option<Vec<String>>,
    mem_mb: Option<u64>,
    cpus: Option<f64>,
    image: Option<String>,
    with_tools: Option<bool>,
}

#[derive(Object, Debug)]
pub struct SessionInfo {
    id: String,
    container_id: String,
    image: String,
    created_at_ms: i64,
    running: bool,
}

#[derive(Object)]
struct ExecRequest {
    cmd: Vec<String>,
    workdir: Option<String>,
    env: Option<Vec<String>>,
    timeout_secs: Option<u64>,
    user: Option<String>,
}

#[derive(Object)]
struct ExecResponse {
    stdout: String,
    stderr: String,
    exit_code: i32,
}

#[derive(ApiResponse)]
enum SessionCreated {
    #[oai(status = 201)]
    Ok(Json<SessionInfo>),
}

#[derive(ApiResponse)]
enum ExecResult {
    #[oai(status = 200)]
    Ok(Json<ExecResponse>),
    #[oai(status = 404)]
    NotFound(PlainText<String>),
    #[oai(status = 400)]
    BadRequest(PlainText<String>),
}

// ---------- VM OpenAPI (secured via API key scopes) ----------

pub struct VmApi;

#[OpenApi]
impl VmApi {
    /// Create a new per-LLM Alpine session. Scope: `vm:create`
    #[oai(path = "/vm/sessions", method = "post")]
    async fn create_session(
        &self,
        state: Data<&AppState>,
        auth: Data<&AuthState>,
        api_key: poem_openapi::param::Header<Option<String>>,
        body: Json<CreateSessionRequest>,
    ) -> poem::Result<SessionCreated> {
        auth.require(&api_key.0, Scope::VmCreate)
            .map_err(|e| poem::Error::from_string(e, StatusCode::UNAUTHORIZED))?;

        let st = state.0.clone();
        let req = body.0;

        // Pull image (once)
        let image = req.image.unwrap_or_else(|| st.image.clone());
        let mut tmp = AppState {
            docker: st.docker.clone(),
            registry: st.registry.clone(),
            network: st.network.clone(),
            image: image.clone(),
            default_mem_mb: st.default_mem_mb,
            default_cpus: st.default_cpus,
        };
        tmp.ensure_image()
            .await
            .map_err(|e| poem::Error::from_string(e, StatusCode::INTERNAL_SERVER_ERROR))?;

        // Prepare container config
        let session_id = uuid::Uuid::new_v4();
        let labels = st.labels_for(session_id);

        let envs = req.env.unwrap_or_default();

        let mem_mb = req.mem_mb.unwrap_or(st.default_mem_mb);
        let cpus = req.cpus.unwrap_or(st.default_cpus);
        let nano_cpus = (cpus * 1_000_000_000f64) as i64;

        let target_net = resolve_network_name(&st.docker, &st.network)
            .await
            .map_err(|e| poem::Error::from_string(e, StatusCode::INTERNAL_SERVER_ERROR))?;

        let host_cfg = HostConfig {
            network_mode: Some(target_net.clone()),
            memory: Some((mem_mb * 1024 * 1024) as i64),
            nano_cpus: Some(nano_cpus),
            ..Default::default()
        };

        let cmd_idle = if req.with_tools.unwrap_or(false) {
            vec!["sh", "-lc", "apk add --no-cache curl jq >/dev/null 2>&1; tail -f /dev/null"]
        } else {
            vec!["sh", "-lc", "tail -f /dev/null"]
        };

        let name = format!("mm-alpine-{}", session_id);
        let cfg = ContainerCreateBody {
            image: Some(image.clone()),
            labels: if labels.is_empty() { None } else { Some(labels.clone()) },
            tty: Some(false),
            open_stdin: Some(false),
            env: if envs.is_empty() { None } else { Some(envs.clone()) },
            host_config: Some(host_cfg),
            cmd: Some(cmd_idle.iter().map(|s| s.to_string()).collect()),
            working_dir: None,
            networking_config: Some(bollard::models::NetworkingConfig {
                endpoints_config: Some(HashMap::from([(
                    target_net.clone(),
                    EndpointSettings {
                        aliases: Some(vec![name.clone()]),
                        ..Default::default()
                    }
                )]))
            }),
            ..Default::default()
        };
        let create_opts = Some(CreateContainerOptions {
            name: Some(name.clone()),
            platform: "".to_string(),
        });

        let create_res = state
            .0
            .docker
            .create_container(create_opts, cfg)
            .await
            .map_err(|e| poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR))?;

        let container_id = create_res.id;

        state
            .0
            .docker
            .start_container(&container_id, None::<StartContainerOptions>)
            .await
            .map_err(|e| poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR))?;

        let created_at_ms = (chrono::Utc::now().timestamp_millis()) as i64;
        let session = Session {
            id: session_id,
            container_id: container_id.clone(),
            image: image.clone(),
            created_at_ms,
        };

        {
            let mut reg = state.0.registry.write().await;
            reg.insert(session_id, session.clone());
        }

        // Confirm it's running
        let running = {
            let inspect = state
                .0
                .docker
                .inspect_container(&container_id, None::<InspectContainerOptions>)
                .await
                .map_err(|e| poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR))?;
            inspect.state.and_then(|s| s.running).unwrap_or(false)
        };

        let info = SessionInfo {
            id: session_id.to_string(),
            container_id,
            image,
            created_at_ms,
            running,
        };

        Ok(SessionCreated::Ok(Json(info)))
    }

    /// List sessions (best-effort running=true). Scope: `vm:read`
    #[oai(path = "/vm/sessions", method = "get")]
    async fn list_sessions(
        &self,
        state: Data<&AppState>,
        auth: Data<&AuthState>,
        api_key: poem_openapi::param::Header<Option<String>>,
    ) -> Json<Vec<SessionInfo>> {
        if let Err(_e) = auth.require(&api_key.0, Scope::VmRead) {
            // For listing, return empty if unauthorized (or change to 401 by switching to poem::Result)
            return Json(Vec::new());
        }
        let reg = state.0.registry.read().await;
        let out = reg
            .values()
            .map(|s| SessionInfo {
                id: s.id.to_string(),
                container_id: s.container_id.clone(),
                image: s.image.clone(),
                created_at_ms: s.created_at_ms,
                running: true,
            })
            .collect();
        Json(out)
    }

    /// Kill & remove a session. Scope: `vm:delete`
    #[oai(path = "/vm/sessions/:id", method = "delete")]
    async fn delete_session(
        &self,
        state: Data<&AppState>,
        auth: Data<&AuthState>,
        api_key: poem_openapi::param::Header<Option<String>>,
        id: poem_openapi::param::Path<String>,
    ) -> poem::Result<PlainText<String>> {
        auth.require(&api_key.0, Scope::VmDelete)
            .map_err(|e| poem::Error::from_string(e, StatusCode::UNAUTHORIZED))?;

        let sid = uuid::Uuid::parse_str(&id.0)
            .map_err(|_| poem::Error::from_string("invalid uuid", StatusCode::BAD_REQUEST))?;

        let container_id = {
            let reg = state.0.registry.read().await;
            reg.get(&sid)
                .map(|s| s.container_id.clone())
                .ok_or_else(|| poem::Error::from_string("not found", StatusCode::NOT_FOUND))?
        };

        let _ = state
            .0
            .docker
            .stop_container(
                &container_id,
                Some(StopContainerOptions { t: Some(2), signal: None }),
            )
            .await;

        state
            .0
            .docker
            .remove_container(
                &container_id,
                Some(RemoveContainerOptions { force: true, ..Default::default() }),
            )
            .await
            .map_err(|e| poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR))?;

        let mut reg = state.0.registry.write().await;
        reg.remove(&sid);

        Ok(PlainText("deleted".into()))
    }

    /// Run a one-shot command in a session. Scope: `vm:exec`
    #[oai(path = "/vm/sessions/:id/exec", method = "post")]
    async fn exec(
        &self,
        state: Data<&AppState>,
        auth: Data<&AuthState>,
        api_key: poem_openapi::param::Header<Option<String>>,
        id: poem_openapi::param::Path<String>,
        body: Json<ExecRequest>,
    ) -> ExecResult {
        if let Err(e) = auth.require(&api_key.0, Scope::VmExec) {
            return ExecResult::BadRequest(PlainText(e));
        }

        let sid = match uuid::Uuid::parse_str(&id.0) {
            Ok(x) => x,
            Err(_) => return ExecResult::BadRequest(PlainText("invalid uuid".into())),
        };

        if body.cmd.is_empty() {
            return ExecResult::BadRequest(PlainText("cmd required".into()));
        }

        let sess = {
            let reg = state.0.registry.read().await;
            match reg.get(&sid) {
                Some(s) => s.clone(),
                None => return ExecResult::NotFound(PlainText("session not found".into())),
            }
        };

        let create = CreateExecOptions {
            cmd: Some(body.cmd.iter().map(|s| s.as_str()).collect()),
            attach_stdout: Some(true),
            attach_stderr: Some(true),
            attach_stdin: Some(false),
            tty: Some(false),
            env: body.env.as_ref().map(|v| v.iter().map(|s| s.as_str()).collect()),
            working_dir: body.workdir.as_deref(),
            user: body.user.as_deref(),
            ..Default::default()
        };

        let exec = match state.0.docker.create_exec(&sess.container_id, create).await {
            Ok(e) => e,
            Err(e) => return ExecResult::BadRequest(PlainText(e.to_string())),
        };

        let timeout = body.timeout_secs.unwrap_or(120);
        let res = tokio::time::timeout(Duration::from_secs(timeout), async {
            match state.0.docker.start_exec(&exec.id, None).await {
                Ok(StartExecResults::Attached { mut output, .. }) => {
                    let mut out_buf = Vec::new();
                    let mut err_buf = Vec::new();

                    while let Some(chunk) = output.try_next().await.map_err(|e| e.to_string())? {
                        use bollard::container::LogOutput;
                        match chunk {
                            LogOutput::StdOut { message } => out_buf.extend_from_slice(&message),
                            LogOutput::StdErr { message } => err_buf.extend_from_slice(&message),
                            LogOutput::Console { message } => out_buf.extend_from_slice(&message),
                            _ => {}
                        }
                    }

                    let inspect = state.0.docker.inspect_exec(&exec.id).await;
                    let code = inspect.ok().and_then(|i| i.exit_code).unwrap_or(0);

                    Ok(ExecResponse {
                        stdout: String::from_utf8_lossy(&out_buf).to_string(),
                        stderr: String::from_utf8_lossy(&err_buf).to_string(),
                        exit_code: code as i32,
                    })
                }
                Ok(_) => Err("unexpected exec result".to_string()),
                Err(e) => Err(e.to_string()),
            }
        })
        .await;

        match res {
            Ok(Ok(payload)) => ExecResult::Ok(Json(payload)),
            Ok(Err(e)) => ExecResult::BadRequest(PlainText(e)),
            Err(_) => ExecResult::BadRequest(PlainText("exec timed out".into())),
        }
    }
}
