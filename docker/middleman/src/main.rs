use poem::{
    endpoint::make, http::StatusCode, listener::TcpListener,
    web::{Data, Path},
    EndpointExt, Request, Response, Route, Server,
};
use poem_openapi::{
    payload::{Json, PlainText},
    ApiResponse, Object, OpenApi, OpenApiService,
};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    env,
    sync::Arc,
    time::Duration,
};
use tokio::sync::RwLock;
use tracing_subscriber;

use bollard::{
    exec::{CreateExecOptions, StartExecResults}, 
    models::{ContainerCreateBody, EndpointSettings, HostConfig}, 
    query_parameters::{
        CreateContainerOptions, CreateImageOptions, InspectContainerOptions,
        InspectNetworkOptions, ListContainersOptions, RemoveContainerOptions, StartContainerOptions,
        StopContainerOptions,
    }, 
    Docker
};

use futures_util::TryStreamExt;
use uuid::Uuid;

// ------------------------ Utilities ------------------------

fn env_or(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn parse_f64_or(s: &str, default: f64) -> f64 {
    s.parse::<f64>().unwrap_or(default)
}
fn parse_u64_or(s: &str, default: u64) -> u64 {
    s.parse::<u64>().unwrap_or(default)
}

// ------------------------ Proxy helpers (unchanged) ------------------------

async fn forward_request(
    req: Request,
    client: &Client,
    target_base: &str,
    strip_prefix: &str,
) -> Response {
    let full_path = req
        .uri()
        .path_and_query()
        .map(|pq| pq.as_str().to_string())
        .unwrap_or_else(|| req.uri().path().to_string());

    let path_after_prefix = if full_path.starts_with(strip_prefix) {
        &full_path[strip_prefix.len()..]
    } else {
        full_path.as_str()
    };

    let url = format!("{target_base}{path_after_prefix}");
    let method = req.method().clone();
    let mut request_builder = client.request(method.clone(), &url);

    for (key, value) in req.headers() {
        let k = key.as_str();
        if k.eq_ignore_ascii_case("host") || k.eq_ignore_ascii_case("connection") {
            continue;
        }
        request_builder = request_builder.header(key, value);
    }

    if let Some(host_val) = req.headers().get("host") {
        request_builder = request_builder.header("x-forwarded-host", host_val.clone());
    }

    let body_bytes = req.into_body().into_bytes().await.unwrap_or_default();
    if !body_bytes.is_empty() {
        request_builder = request_builder.body(body_bytes);
    }

    println!("Forwarding {} {} -> {}", method, path_after_prefix, url);

    match request_builder.send().await {
        Ok(resp) => {
            let status = resp.status();
            let mut response_builder = Response::builder().status(status);
            for (key, value) in resp.headers() {
                response_builder = response_builder.header(key, value.clone());
            }
            let body = resp.bytes().await.unwrap_or_default();
            response_builder.body(body)
        }
        Err(e) => {
            eprintln!("Error forwarding request to {url}: {e}");
            Response::builder()
                .status(StatusCode::BAD_GATEWAY)
                .body("Upstream Error")
        }
    }
}

// ------------------------ VM / Alpine management ------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Session {
    id: Uuid,
    container_id: String,
    image: String,
    created_at_ms: i64,
}

#[derive(Clone)]
struct AppState {
    docker: Docker,
    // Authoritative in-memory registry (rebuilt at boot from labels)
    registry: Arc<RwLock<HashMap<Uuid, Session>>>,
    network: String,
    image: String,
    default_mem_mb: u64,
    default_cpus: f64,
}

impl AppState {
    async fn ensure_image(&self) -> Result<(), String> {
        // Try to pull the image; if already present, this is fast/no-op.
        let options = Some(CreateImageOptions {
            from_image: Some(self.image.clone()),
            ..Default::default()
        });
        let mut stream = self.docker.create_image(options, None, None);
        while let Some(_chunk) = stream.try_next().await.map_err(|e| e.to_string())? {
            // Swallow logs; uncomment for verbose pulls:
            // eprintln!("pull: {:?}", chunk);
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

    async fn rebuild_registry_from_docker(&self) -> Result<(), String> {
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
                        let image = c.image.unwrap_or_else(|| self.image.clone());
                        let container_id = c.id.unwrap_or_default();
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

// ---------- OpenAPI types ----------

#[derive(Object)]
struct CreateSessionRequest {
    /// Optional environment variables like `KEY=VALUE`
    env: Option<Vec<String>>,
    /// Optional memory (MB). Default from ALPINE_MEM_MB.
    mem_mb: Option<u64>,
    /// Optional CPUs (e.g. 0.5 = half a CPU). Default from ALPINE_CPUS.
    cpus: Option<f64>,
    /// Optional image override (defaults to ALPINE_IMAGE)
    image: Option<String>,
    /// If true, pre-install some helpful tools (curl, jq)
    with_tools: Option<bool>,
}

#[derive(Object, Debug)]
struct SessionInfo {
    id: String,
    container_id: String,
    image: String,
    created_at_ms: i64,
    running: bool,
}

#[derive(Object)]
struct ExecRequest {
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

// ---------- OpenAPI: core (your existing) ----------

struct CoreApi;

#[OpenApi]
impl CoreApi {
    #[oai(path = "/hello", method = "get")]
    async fn index(&self, name: poem_openapi::param::Query<Option<String>>) -> PlainText<String> {
        match name.0 {
            Some(name) => PlainText(format!("hello, {name}!")),
            None => PlainText("hello!".to_string()),
        }
    }

    #[oai(path = "/health", method = "get")]
    async fn health(&self) -> PlainText<String> {
        PlainText("OK".to_string())
    }
}

// ---------- OpenAPI: VM/Alpine management ----------

struct VmApi;

#[OpenApi]
impl VmApi {
    /// Create a new per-LLM Alpine session.
    #[oai(path = "/vm/sessions", method = "post")]
    async fn create_session(
        &self,
        state: Data<&AppState>,
        body: Json<CreateSessionRequest>,
    ) -> poem::Result<SessionCreated> {
        let st = state.0.clone();
        let req = body.0;

        // Pull image (once)
        let image = req.image.unwrap_or_else(|| st.image.clone());
        // Temporarily make an ad-hoc state with overridden image
        let mut tmp = AppState {
            docker: st.docker.clone(),
            registry: st.registry.clone(),
            network: st.network.clone(),
            image: image.clone(),
            default_mem_mb: st.default_mem_mb,
            default_cpus: st.default_cpus,
        };
        tmp.image = image.clone();
        tmp.ensure_image()
            .await
            .map_err(|e| poem::Error::from_string(e, StatusCode::INTERNAL_SERVER_ERROR))?;

        // Prepare container config
        let session_id = Uuid::new_v4();
        let labels = st.labels_for(session_id);

        // Keep container idle for the session lifetime
        let envs = req.env.unwrap_or_default();
        // Helpful defaults so commands route through the middleman if you want proxying later
        // envs.push(format!("HTTP_PROXY=http://middleman:{}", env_or("MIDDLEMAN_PORT","3000")));
        // envs.push(format!("HTTPS_PROXY=http://middleman:{}", env_or("MIDDLEMAN_PORT","3000")));

        let mem_mb = req.mem_mb.unwrap_or(st.default_mem_mb);
        let cpus = req.cpus.unwrap_or(st.default_cpus);
        let nano_cpus = (cpus * 1_000_000_000f64) as i64;

        let target_net = resolve_network_name(
            &st.docker,
            &st.network,
        ).await.map_err(|e| poem::Error::from_string(e, StatusCode::INTERNAL_SERVER_ERROR))?;

        let host_cfg = HostConfig {
            network_mode: Some(target_net.clone()),
            memory: Some((mem_mb * 1024 * 1024) as i64),
            nano_cpus: Some(nano_cpus),
            // Security hardening ideas (tune as you like)
            // read_only_rootfs: Some(true),
            // cap_drop: Some(vec!["ALL".into()]),
            ..Default::default()
        };

        let cmd_idle = if req.with_tools.unwrap_or(false) {
            // We'll bootstrap tools on first start with a small sh -lc script
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
        let session = Session {
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

        println!("Created session {:#?}", info);

        Ok(SessionCreated::Ok(Json(info)))
    }

    /// List sessions middleman knows about (reconciled at boot from labels).
    #[oai(path = "/vm/sessions", method = "get")]
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
    #[oai(path = "/vm/sessions/:id", method = "delete")]
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
            .stop_container(&container_id, Some(StopContainerOptions { 
                t: Some(2),
                signal: None,
            }))
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
            .map_err(|e| poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR))?;

        let mut reg = st.registry.write().await;
        reg.remove(&sid);

        Ok(PlainText("deleted".into()))
    }

    /// Run a one-shot command inside a session (stdout/stderr/exit).
    #[oai(path = "/vm/sessions/:id/exec", method = "post")]
    async fn exec(
        &self,
        state: Data<&AppState>,
        Path(id): Path<String>,
        body: Json<ExecRequest>,
    ) -> ExecResult {
        let st = state.0;
        let sid = match Uuid::parse_str(&id) {
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
}

async fn resolve_network_name(docker: &bollard::Docker, desired: &str) -> Result<String, String> {
    // First try the desired name
    if docker.inspect_network(desired, None::<InspectNetworkOptions>).await.is_ok() {
        return Ok(desired.to_string());
    }

    // Fallback: use the network(s) this container is already attached to
    let self_id = std::env::var("HOSTNAME").unwrap_or_default(); // in Docker, HOSTNAME is the container ID
    let me = docker.inspect_container(&self_id, None::<InspectContainerOptions>)
        .await
        .map_err(|e| format!("inspect self failed: {e}"))?;

    if let Some(netmap) = me.network_settings.and_then(|ns| ns.networks) {
        if let Some((name, _)) = netmap.into_iter().next() {
            return Ok(name);
        }
    }
    Err(format!("network '{desired}' not found, and could not infer a fallback from self"))
}


// ------------------------ main ------------------------

#[tokio::main]
async fn main() -> Result<(), std::io::Error> {
    tracing_subscriber::fmt::init();

    // Bind address / ports
    let port = env_or("MIDDLEMAN_PORT", "3000");
    let local_address = env_or("LOCAL_ADDRESS", "0.0.0.0");
    let bind_addr = format!("{}:{}", local_address, port);

    // Route slugs
    let fhir_route = env_or("FHIR_ROUTE", "fhir_server");
    let terminology_route = env_or("TERMINOLOGY_ROUTE", "terminology_server");
    let validation_route = env_or("VALIDATION_ROUTE", "validation_server");
    let openfda_route = env_or("OPENFDA_ROUTE", "openfda_api");
    let mcp_route = env_or("MCP_ROUTE", "mcp_server");
    let sandbox_route = env_or("SANDBOX_ROUTE", "sandbox_server");

    // Upstream bases
    let fhir_upstream = env_or("FHIR_UPSTREAM_BASE", "http://hapi:8080");
    let terminology_upstream = env_or("TERMINOLOGY_UPSTREAM_BASE", "http://tx.fhir.org/r4");
    let validation_upstream = env_or("VALIDATION_UPSTREAM_BASE", "http://validator:3500");
    let openfda_upstream = env_or("OPENFDA_UPSTREAM_BASE", "https://api.fda.gov");
    let mcp_upstream = env_or("MCP_UPSTREAM_BASE", "http://mcp:8000/mcp");
    let sandbox_upstream = env_or("SANDBOX_UPSTREAM_BASE", "http://sandbox:8088");

    // HTTP client for proxying
    let client = Arc::new(
        reqwest::ClientBuilder::new()
            .timeout(Duration::from_secs(300))
            .connect_timeout(Duration::from_secs(5))
            .pool_max_idle_per_host(64)
            .tcp_keepalive(Some(Duration::from_secs(30)))
            .build()
            .expect("failed building reqwest client"),
    );

    // NEW: Docker client + app state
    let docker =
        Docker::connect_with_unix_defaults().expect("failed to connect to /var/run/docker.sock");
    let network = env_or("DOCKER_NETWORK", "medschool-net");
    let image = env_or("ALPINE_IMAGE", "alpine:3.20");
    let default_mem_mb = parse_u64_or(&env_or("ALPINE_MEM_MB", "256"), 256);
    let default_cpus = parse_f64_or(&env_or("ALPINE_CPUS", "0.5"), 0.5);

    let state = AppState {
        docker,
        registry: Arc::new(RwLock::new(HashMap::new())),
        network,
        image,
        default_mem_mb,
        default_cpus,
    };
    // Pre-pull default image and reconcile any already-running sessions
    let _ = state.ensure_image().await;
    let _ = state.rebuild_registry_from_docker().await;

    // OpenAPI services (combine both)
    let api_service = OpenApiService::new((CoreApi, VmApi), "Middleman", "1.1")
        .server(&format!("http://{}/api", bind_addr));
    let ui = api_service.swagger_ui();

    // Prefixes
    let fhir_prefix = format!("/{}", fhir_route);
    let terminology_prefix = format!("/{}", terminology_route);
    let validation_prefix = format!("/{}", validation_route);
    let openfda_prefix = format!("/{}", openfda_route);
    let mcp_prefix = format!("/{}", mcp_route);
    let sandbox_prefix = format!("/{}", sandbox_route);

    // Handlers
    let c1 = client.clone();
    let fhir_handler = make(move |req| {
        let c = c1.clone();
        let upstream = fhir_upstream.clone();
        let prefix = fhir_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix).await }
    });

    let c2 = client.clone();
    let terminology_handler = make(move |req| {
        let c = c2.clone();
        let upstream = terminology_upstream.clone();
        let prefix = terminology_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix).await }
    });

    let c3 = client.clone();
    let validation_handler = make(move |req| {
        let c = c3.clone();
        let upstream = validation_upstream.clone();
        let prefix = validation_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix).await }
    });

    let c4 = client.clone();
    let openfda_handler = make(move |req| {
        let c = c4.clone();
        let upstream = openfda_upstream.clone();
        let prefix = openfda_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix).await }
    });

    let c5 = client.clone();
    let mcp_handler = make(move |req| {
        let c = c5.clone();
        let upstream = mcp_upstream.clone();
        let prefix = mcp_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix).await }
    });

    let c6 = client.clone();
    let sandbox_handler = make(move |req| {
        let c = c6.clone();
        let upstream = sandbox_upstream.clone();
        let prefix = sandbox_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix).await }
    });

    Server::new(TcpListener::bind(&bind_addr))
        .run(
            Route::new()
                .nest("/api", api_service)
                .nest("/docs", ui)
                .at(&format!("/{fhir_route}/*"), fhir_handler)
                .at(&format!("/{terminology_route}/*"), terminology_handler)
                .at(&format!("/{validation_route}/*"), validation_handler)
                .at(&format!("/{openfda_route}/*"), openfda_handler)
                .at(&format!("/{mcp_route}/*"), mcp_handler)
                .at(&format!("/{sandbox_route}/*"), sandbox_handler)
                // NOTE: the old QEMU WS bridge is removed; interactive TTY can be added later if needed
                .data(state),
        )
        .await
}
