mod session;
mod terminal;
mod vm;

use poem::{
    endpoint::make,
    http::StatusCode,
    listener::TcpListener,
    EndpointExt, Request, Response, Route, Server,
};
use poem_openapi::{
    payload::PlainText,
    Object, OpenApi, OpenApiService,
};
use reqwest::Client;
use serde::Serialize;
use std::{collections::HashMap, env, sync::Arc, time::Duration};
use tokio::sync::RwLock;
use tracing_subscriber;

use bollard::{
    query_parameters::{CreateImageOptions, ListContainersOptions},
    Docker,
};

use futures_util::TryStreamExt;
use uuid::Uuid;

use crate::{session::{Session, FhirQuery, ToolCall}, vm::VmSession};

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

#[derive(PartialEq)]
enum RequestType {
    Fhir,
    Tool,
    Terminal
}

async fn forward_request(
    req: Request,
    client: &Client,
    target_base: &str,
    strip_prefix: &str,
    request_type: RequestType,
    state: &AppState
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

    // extract the session ID from the header
    let session_id = req.headers().get("x-session-id").cloned();

    let url = format!("{target_base}{path_after_prefix}");
    let method = req.method().clone();
    let mut request_builder = client.request(method.clone(), &url);

    for (key, value) in req.headers() {
        let k = key.as_str();
        if k.eq_ignore_ascii_case("host") || k.eq_ignore_ascii_case("connection") {
            continue;
        }
        if k.eq_ignore_ascii_case("x-session-id") && request_type != RequestType::Terminal {
            continue; // skip our custom header for non-terminal requests
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

    let request_result = match request_builder.send().await {
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
    };

    let error = if request_result.status().is_success() { None } else { Some("Request failed".to_string()) };

    if let Some(session_id) = session_id {
        if let Ok(sid) = session_id.to_str() {
            let mut sessions = state.sessions.write().await;
            if let Some(session) = sessions.get_mut(sid) {
                match request_type {
                    RequestType::Fhir => {
                        session.fhir_queries.push(FhirQuery {
                            method: method.to_string(),
                            url: url.clone(),
                            error: error.clone(),
                        });
                    }
                    RequestType::Tool => {
                        session.tool_calls.push(ToolCall {
                            tool_name: "proxy".to_string(),
                            input: url.clone(),
                            output: "".to_string(),
                            error: error.clone(),
                        });
                    }
                    RequestType::Terminal => {
                        session.terminal_commands.push(url.clone());
                    }
                }
            }
        }
    }

    match request_type {
        RequestType::Fhir => {
            println!("FHIR Request: {} {}", method, path_after_prefix);
            // Log the FHIR request in the session
            
        }
        RequestType::Tool => {
            println!("Tool Request: {} {}", method, path_after_prefix);
        }
        RequestType::Terminal => {
            println!("Terminal Request: {} {}", method, path_after_prefix);
        }
    }

    request_result
}

// ------------------------ VM / Alpine management ------------------------

#[derive(Clone)]
struct AppState {
    docker: Docker,
    // Authoritative in-memory registry (rebuilt at boot from labels)
    registry: Arc<RwLock<HashMap<Uuid, VmSession>>>,
    network: String,
    image: String,
    default_mem_mb: u64,
    default_cpus: f64,
    sessions: Arc<RwLock<HashMap<String, Session>>>,
}

impl AppState {
    async fn ensure_image(&self) -> Result<(), String> {
        // For local images (no registry prefix), assume they exist if built by docker-compose
        if !self.image.contains('/') {
            return Ok(());
        }

        // For remote images, try to pull them
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
                            VmSession {
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

#[derive(Object, Clone)]
struct CreateSessionRequest {
    /// Optional environment variables like `KEY=VALUE`
    env: Option<Vec<String>>,
    /// Optional memory (MB). Default from ALPINE_MEM_MB.
    mem_mb: Option<u64>,
    /// Optional CPUs (e.g. 0.5 = half a CPU). Default from ALPINE_CPUS.
    cpus: Option<f64>,
    /// Optional image override (defaults to ALPINE_IMAGE)
    image: Option<String>,
}

#[derive(Object, Debug, Clone, Serialize)]
struct SessionInfo {
    id: String,
    container_id: String,
    image: String,
    created_at_ms: i64,
    running: bool,
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
    let terminal_route = env_or("TERMINAL_ROUTE", "terminal");

    // Upstream bases
    let fhir_upstream = env_or("FHIR_UPSTREAM_BASE", "http://hapi:8080");
    let terminology_upstream = env_or("TERMINOLOGY_UPSTREAM_BASE", "http://tx.fhir.org/r4");
    let validation_upstream = env_or("VALIDATION_UPSTREAM_BASE", "http://validator:3500");
    let openfda_upstream = env_or("OPENFDA_UPSTREAM_BASE", "https://api.fda.gov");
    let mcp_upstream = env_or("MCP_UPSTREAM_BASE", "http://mcp:8000/mcp");
    let sandbox_upstream = env_or("SANDBOX_UPSTREAM_BASE", "http://sandbox:8088");
    let terminal_upstream = env_or("TERMINAL_UPSTREAM_BASE", "http://middleman:3000/interminal"); // interminal == internal terminal handling

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

    // TODO: make sure these align with compose / env vars
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
        sessions: Arc::new(RwLock::new(HashMap::new())),
    };
    // Pre-pull default image and reconcile any already-running sessions
    let _ = state.ensure_image().await;
    let _ = state.rebuild_registry_from_docker().await;

    let state_clone = state.clone();

    // OpenAPI services (combine both)
    let vm_service = OpenApiService::new((CoreApi, crate::vm::VmApi), "Middleman VM", "1.1")
        .server(&format!("http://{}/vm", bind_addr));
    let vm_ui = vm_service.swagger_ui();

    let session_service = OpenApiService::new((CoreApi, crate::session::SessionApi), "Middleman Sessions", "1.0")
        .server(&format!("http://{}/sessions", bind_addr));
    let session_ui = session_service.swagger_ui();

    let terminal_service = OpenApiService::new((CoreApi, crate::terminal::TerminalApi), "Middleman Terminal", "1.0")
        .server(&format!("http://{}/interminal", bind_addr));
    let terminal_ui = terminal_service.swagger_ui();

    // Prefixes
    let fhir_prefix = format!("/{}", fhir_route);
    let terminology_prefix = format!("/{}", terminology_route);
    let validation_prefix = format!("/{}", validation_route);
    let openfda_prefix = format!("/{}", openfda_route);
    let mcp_prefix = format!("/{}", mcp_route);
    let sandbox_prefix = format!("/{}", sandbox_route);
    let terminal_prefix = format!("/{}", terminal_route);

    // Handlers
    let c1 = client.clone();
    let s1 = state_clone.clone();
    let fhir_handler = make(move |req| {
        let c = c1.clone();
        let s = s1.clone();
        let upstream = fhir_upstream.clone();
        let prefix = fhir_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix, RequestType::Fhir, &s).await }
    });

    let c2 = client.clone();
    let s2 = state_clone.clone();
    let terminology_handler = make(move |req| {
        let c = c2.clone();
        let s = s2.clone();
        let upstream = terminology_upstream.clone();
        let prefix = terminology_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix, RequestType::Tool, &s).await }
    });

    let c3 = client.clone();
    let s3 = state_clone.clone();
    let validation_handler = make(move |req| {
        let c = c3.clone();
        let s = s3.clone();
        let upstream = validation_upstream.clone();
        let prefix = validation_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix, RequestType::Tool, &s).await }
    });

    let c4 = client.clone();
    let s4 = state_clone.clone();
    let openfda_handler = make(move |req| {
        let c = c4.clone();
        let s = s4.clone();
        let upstream = openfda_upstream.clone();
        let prefix = openfda_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix, RequestType::Tool, &s).await }
    });

    let c5 = client.clone();
    let s5 = state_clone.clone();
    let mcp_handler = make(move |req| {
        let c = c5.clone();
        let s = s5.clone();
        let upstream = mcp_upstream.clone();
        let prefix = mcp_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix, RequestType::Tool, &s).await }
    });

    let c6 = client.clone();
    let s6 = state_clone.clone();
    let sandbox_handler = make(move |req| {
        let c = c6.clone();
        let s = s6.clone();
        let upstream = sandbox_upstream.clone();
        let prefix = sandbox_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix, RequestType::Tool, &s).await }
    });

    let c7 = client.clone();
    let s7 = state_clone.clone();
    let terminal_handler = make(move |req| {
        let c = c7.clone();
        let s = s7.clone();
        let upstream = terminal_upstream.clone();
        let prefix = terminal_prefix.clone();
        async move { forward_request(req, &c, &upstream, &prefix, RequestType::Terminal, &s).await }
    });
        

    Server::new(TcpListener::bind(&bind_addr))
        .run(
            Route::new()
                .nest("/vm", vm_service)
                .nest("/vm_docs", vm_ui)
                .nest("/sessions", session_service)
                .nest("/sessions_docs", session_ui)
                .nest("/interminal", terminal_service)
                .nest("/terminal_docs", terminal_ui)
                .at(&format!("/{fhir_route}/*"), fhir_handler)
                .at(&format!("/{terminology_route}/*"), terminology_handler)
                .at(&format!("/{validation_route}/*"), validation_handler)
                .at(&format!("/{openfda_route}/*"), openfda_handler)
                .at(&format!("/{mcp_route}/*"), mcp_handler)
                .at(&format!("/{sandbox_route}/*"), sandbox_handler)
                .at(&format!("/{terminal_route}"), terminal_handler)
                // NOTE: the old QEMU WS bridge is removed; interactive TTY can be added later if needed
                .data(state),
        )
        .await
}
