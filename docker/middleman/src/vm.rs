use std::{collections::HashMap, time::Duration};

use anyhow::Result;

use bollard::{
    exec::{CreateExecOptions, StartExecResults},
    query_parameters::{
        CreateContainerOptions, InspectContainerOptions, InspectNetworkOptions,
        RemoveContainerOptions, StartContainerOptions, StopContainerOptions,
    },
    secret::{ContainerCreateBody, EndpointSettings, HostConfig},
};
use futures_util::TryStreamExt;
use poem::{
    http::StatusCode,
    web::{Data, Path},
};
use poem_openapi::{
    payload::{Json, PlainText},
    ApiResponse, Object, OpenApi,
};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{AppState, CreateSessionRequest, SessionInfo};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VmSession {
    pub id: Uuid,
    pub container_id: String,
    pub image: String,
    pub created_at_ms: i64,
}

#[derive(Object)]
pub struct ExecRequest {
    /// Command array, e.g. ["sh","-lc","echo hi"]
    cmd: Vec<String>,
    /// Optional working directory
    workdir: Option<String>,
    /// Optional environment like ["FOO=bar"]
    env: Option<Vec<String>>,
    /// Seconds to wait before we cancel (server-side)
    timeout_secs: Option<u64>,
    /// Run as this user inside the container (e.g. "nobody")
    user: Option<String>,
}

#[derive(Object)]
pub struct ExecResponse {
    stdout: String,
    stderr: String,
    exit_code: i32,
}

#[derive(ApiResponse)]
pub enum ExecResult {
    #[oai(status = 200)]
    Ok(Json<ExecResponse>),
    #[oai(status = 404)]
    NotFound(PlainText<String>),
    #[oai(status = 400)]
    BadRequest(PlainText<String>),
}

pub struct VmApi;

#[OpenApi]
impl VmApi {
    /// List sessions middleman knows about (reconciled at boot from labels).
    #[oai(path = "/sessions", method = "get")]
    async fn list_sessions(&self, state: Data<&AppState>) -> Json<Vec<SessionInfo>> {
        let st = state.0;
        let reg = st.registry.read().await;
        let mut out = Vec::with_capacity(reg.len());
        for sess in reg.values() {
            out.push(SessionInfo {
                id: sess.id.to_string(),
                container_id: sess.container_id.clone(),
                image: sess.image.clone(),
                created_at_ms: sess.created_at_ms,
                running: true, // cheap default; for exactness we could inspect each
            });
        }
        Json(out)
    }

    /// Kill & remove a session's container.
    #[oai(path = "/sessions/:id", method = "delete")]
    async fn delete_session(
        &self,
        state: Data<&AppState>,
        Path(id): Path<String>,
    ) -> poem::Result<PlainText<String>> {
        let st = state.0;
        let sid = Uuid::parse_str(&id)
            .map_err(|_| poem::Error::from_string("invalid uuid", StatusCode::BAD_REQUEST))?;

        let container_id = {
            let reg = st.registry.read().await;
            reg.get(&sid)
                .map(|s| s.container_id.clone())
                .ok_or_else(|| poem::Error::from_string("not found", StatusCode::NOT_FOUND))?
        };

        // Try to stop (best-effort), then remove
        let _ = st
            .docker
            .stop_container(
                &container_id,
                Some(StopContainerOptions {
                    t: Some(2),
                    signal: None,
                }),
            )
            .await;
        st.docker
            .remove_container(
                &container_id,
                Some(RemoveContainerOptions {
                    force: true,
                    ..Default::default()
                }),
            )
            .await
            .map_err(|e| {
                poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR)
            })?;

        let mut reg = st.registry.write().await;
        reg.remove(&sid);

        Ok(PlainText("deleted".into()))
    }
}

async fn resolve_network_name(docker: &bollard::Docker, desired: &str) -> Result<String, String> {
    // First try the desired name
    if docker
        .inspect_network(desired, None::<InspectNetworkOptions>)
        .await
        .is_ok()
    {
        return Ok(desired.to_string());
    }

    // Fallback: use the network(s) this container is already attached to
    let self_id = std::env::var("HOSTNAME").unwrap_or_default(); // in Docker, HOSTNAME is the container ID
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

pub async fn create_session(
    // &self,
    state: &AppState,
    body: CreateSessionRequest,
) -> Result<SessionInfo> {
    let st = state.clone();

    // Pull image (once)
    let image = body.image.unwrap_or_else(|| st.image.clone());
    // Temporarily make an ad-hoc state with overridden image
    let mut tmp = AppState {
        docker: st.docker.clone(),
        registry: st.registry.clone(),
        network: st.network.clone(),
        image: image.clone(),
        default_mem_mb: st.default_mem_mb,
        default_cpus: st.default_cpus,
        sessions: st.sessions.clone(),
    };
    tmp.image = image.clone();
    tmp.ensure_image()
        .await
        .map_err(|e| poem::Error::from_string(e, StatusCode::INTERNAL_SERVER_ERROR))?;

    // Prepare container config
    let session_id = Uuid::new_v4();
    let labels = st.labels_for(session_id);

    // Keep container idle for the session lifetime
    let envs = body.env.unwrap_or_default();
    // Helpful defaults so commands route through the middleman if you want proxying later
    // envs.push(format!("HTTP_PROXY=http://middleman:{}", env_or("MIDDLEMAN_PORT","3000")));
    // envs.push(format!("HTTPS_PROXY=http://middleman:{}", env_or("MIDDLEMAN_PORT","3000")));

    let mem_mb = body.mem_mb.unwrap_or(st.default_mem_mb);
    let cpus = body.cpus.unwrap_or(st.default_cpus);
    let nano_cpus = (cpus * 1_000_000_000f64) as i64;

    let target_net = resolve_network_name(&st.docker, &st.network)
        .await
        .map_err(|e| poem::Error::from_string(e, StatusCode::INTERNAL_SERVER_ERROR))?;

    let host_cfg = HostConfig {
        network_mode: Some(target_net.clone()),
        memory: Some((mem_mb * 1024 * 1024) as i64),
        nano_cpus: Some(nano_cpus),
        // Security hardening ideas (tune as you like)
        // read_only_rootfs: Some(true),
        // cap_drop: Some(vec!["ALL".into()]),
        ..Default::default()
    };

    // Since tools are pre-installed in the custom Alpine image, just run the idle command
    let cmd_idle = vec!["sh", "-lc", "tail -f /dev/null"];

    let name = format!("mm-alpine-{}", session_id);
    let cfg = ContainerCreateBody {
        image: Some(image.clone()),
        labels: if labels.is_empty() {
            None
        } else {
            Some(labels.clone())
        },
        tty: Some(false),
        open_stdin: Some(false),
        env: if envs.is_empty() {
            None
        } else {
            Some(envs.clone())
        },
        host_config: Some(host_cfg),
        cmd: Some(cmd_idle.iter().map(|s| s.to_string()).collect()),
        working_dir: None,
        networking_config: Some(bollard::models::NetworkingConfig {
            endpoints_config: Some(HashMap::from([(
                target_net.clone(),
                EndpointSettings {
                    aliases: Some(vec![name.clone()]),
                    ..Default::default()
                },
            )])),
        }),
        ..Default::default()
    };
    let create_opts = Some(CreateContainerOptions {
        name: Some(name.clone()),
        platform: "".to_string(),
    });

    let create_res = st
        .docker
        .create_container(create_opts, cfg)
        .await
        .map_err(|e| poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR))?;

    let container_id = create_res.id;

    st.docker
        .start_container(&container_id, None::<StartContainerOptions>)
        .await
        .map_err(|e| poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR))?;

    let created_at_ms = (chrono::Utc::now().timestamp_millis()) as i64;
    let session = VmSession {
        id: session_id,
        container_id: container_id.clone(),
        image: image.clone(),
        created_at_ms,
    };

    {
        let mut reg = st.registry.write().await;
        reg.insert(session_id, session.clone());
    }

    // Confirm it's running
    let running = {
        let inspect = st
            .docker
            .inspect_container(&container_id, None::<InspectContainerOptions>)
            .await
            .map_err(|e| {
                poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR)
            })?;
        inspect.state.and_then(|s| s.running).unwrap_or(false)
    };

    let info = SessionInfo {
        id: session_id.to_string(),
        container_id,
        image,
        created_at_ms,
        running,
    };

    println!("Created session {:#?}", info);

    Ok(info)
}

/// Run a one-shot command inside a session (stdout/stderr/exit).
// #[oai(path = "/sessions/:id/exec", method = "post")]
pub async fn exec(
    // &self,
    state: Data<&AppState>,
    session_id: &str,
    body: Json<ExecRequest>,
) -> ExecResult {
    let st = state.0;
    let sid = match Uuid::parse_str(&session_id) {
        Ok(x) => x,
        Err(_) => return ExecResult::BadRequest(PlainText("invalid uuid".into())),
    };
    let sess = {
        let reg = st.registry.read().await;
        match reg.get(&sid) {
            Some(s) => s.clone(),
            None => return ExecResult::NotFound(PlainText("session not found".into())),
        }
    };

    if body.cmd.is_empty() {
        return ExecResult::BadRequest(PlainText("cmd required".into()));
    }

    // Automatically wrap commands in shell if they don't already use one
    let cmd = if body.cmd.len() == 1 && !body.cmd[0].starts_with("sh") && !body.cmd[0].starts_with("bash") {
        // Single command that doesn't use shell - wrap it
        vec!["sh".to_string(), "-c".to_string(), body.cmd[0].clone()]
    } else if body.cmd.len() > 1 && body.cmd[0] != "sh" && body.cmd[0] != "bash" {
        // Multi-part command that doesn't start with shell - wrap it
        vec!["sh".to_string(), "-c".to_string(), body.cmd.join(" ")]
    } else {
        // Already using shell or single shell command
        body.cmd.clone()
    };

    let create = CreateExecOptions {
        cmd: Some(cmd.iter().map(|s| s.as_str()).collect()),
        attach_stdout: Some(true),
        attach_stderr: Some(true),
        attach_stdin: Some(false),
        tty: Some(false),
        env: body
            .env
            .as_ref()
            .map(|v| v.iter().map(|s| s.as_str()).collect()),
        working_dir: body.workdir.as_deref(),
        user: body.user.as_deref(),
        ..Default::default()
    };

    let exec = match st.docker.create_exec(&sess.container_id, create).await {
        Ok(e) => e,
        Err(e) => return ExecResult::BadRequest(PlainText(e.to_string())),
    };

    let timeout = body.timeout_secs.unwrap_or(120);
    let res = tokio::time::timeout(Duration::from_secs(timeout), async {
        match st.docker.start_exec(&exec.id, None).await {
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

                let inspect = st.docker.inspect_exec(&exec.id).await;
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
