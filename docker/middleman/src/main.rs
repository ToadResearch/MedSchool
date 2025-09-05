// use futures_util::{SinkExt, StreamExt};
use poem::{
    endpoint::make, get, http::StatusCode, listener::TcpListener, web::{
        websocket::{Message as WsMsg, WebSocket}, Data, Path
    }, EndpointExt, IntoResponse, Request, Response, Route, Server
};
use poem_openapi::{param::Query, payload::PlainText, OpenApi, OpenApiService};
use reqwest::Client;
use std::{env, sync::Arc, time::Duration};
// use tokio_tungstenite::connect_async;
// use tokio_tungstenite::tungstenite::Message as TMsg;

struct Api;

#[OpenApi]
impl Api {
    #[oai(path = "/hello", method = "get")]
    async fn index(&self, name: Query<Option<String>>) -> PlainText<String> {
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

fn env_or(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

// TODO: maybe also log the args of the request
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

    // Copy headers, excluding host/connection (reqwest will set Host for the new target)
    for (key, value) in req.headers() {
        let k = key.as_str();
        if k.eq_ignore_ascii_case("host") || k.eq_ignore_ascii_case("connection") {
            continue;
        }
        request_builder = request_builder.header(key, value);
    }

    // Optionally pass-through original Host as x-forwarded-host (read from headers)
    if let Some(host_val) = req.headers().get("host") {
        request_builder = request_builder.header("x-forwarded-host", host_val.clone());
    }
    // If the client already sent x-forwarded-for, it will be forwarded by the loop above.

    // Body
    let body_bytes = req.into_body().into_bytes().await.unwrap_or_default();
    if !body_bytes.is_empty() {
        request_builder = request_builder.body(body_bytes);
    }

    println!("Forwarding {} {} -> {}", method, path_after_prefix, url);

    match request_builder.send().await {
        Ok(resp) => {
            let status = resp.status();
            let mut response_builder = Response::builder().status(status);

            // Copy response headers through
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

fn http_base_to_ws(base: &str) -> String {
    if let Some(rest) = base.strip_prefix("https://") {
        format!("wss://{}", rest)
    } else if let Some(rest) = base.strip_prefix("http://") {
        format!("ws://{}", rest)
    } else {
        // Fallback: assume it's already a ws(s) URL
        base.to_string()
    }
}

fn bridge_ws(vm_ws_base: String, id: String, ws: WebSocket) -> impl IntoResponse {
    use futures_util::{SinkExt, StreamExt};
    use tokio_tungstenite::{connect_async, tungstenite::Message as TMsg};

    let upstream_url = format!("{}/sessions/{}/tty", vm_ws_base, id);

    ws.on_upgrade(move |client_ws| async move {
        match connect_async(&upstream_url).await {
            Ok((upstream_ws, _resp)) => {
                let (mut upstream_write, mut upstream_read) = upstream_ws.split();
                let (mut client_write, mut client_read) = client_ws.split();

                // Client → upstream
                let c2u = async {
                    while let Some(Ok(msg)) = client_read.next().await {
                        let out = match msg {
                            WsMsg::Text(t)   => TMsg::Text(t.into()),
                            WsMsg::Binary(b) => TMsg::Binary(b.into()),
                            WsMsg::Ping(p)   => TMsg::Ping(p.into()),
                            WsMsg::Pong(p)   => TMsg::Pong(p.into()),
                            WsMsg::Close(_)  => TMsg::Close(None),
                        };
                        if upstream_write.send(out).await.is_err() {
                            break;
                        }
                    }
                    let _ = upstream_write.close().await;
                };

                // Upstream → client
                let u2c = async {
                    while let Some(Ok(msg)) = upstream_read.next().await {
                        let out = match msg {
                            TMsg::Text(t)   => WsMsg::Text(t.to_string()),
                            TMsg::Binary(b) => WsMsg::Binary(b.to_vec()),
                            TMsg::Ping(p)   => WsMsg::Ping(p.to_vec()),
                            TMsg::Pong(p)   => WsMsg::Pong(p.to_vec()),
                            TMsg::Close(_)  => WsMsg::Close(None),
                            _ => continue,
                        };
                        if client_write.send(out).await.is_err() {
                            break;
                        }
                    }
                    let _ = client_write.close().await;
                };

                tokio::select! { _ = c2u => {}, _ = u2c => {} }
            }
            Err(e) => {
                eprintln!("WS connect to vm_handler failed: {e}");
            }
        }
    })
}

#[poem::handler]
async fn vm_tty_ws(
    Path(id): Path<String>,
    ws: WebSocket,
    Data(vm_ws_base): Data<&String>,
) -> impl IntoResponse {
    bridge_ws(vm_ws_base.clone(), id, ws)
}


#[tokio::main]
async fn main() -> Result<(), std::io::Error> {
    tracing_subscriber::fmt::init();

    // Bind (in Compose, set MIDDLEMAN_PORT to the CONTAINER port)
    let port = env_or("MIDDLEMAN_PORT", "3000");
    let local_address = env_or("LOCAL_ADDRESS", "0.0.0.0");
    let bind_addr = format!("{}:{}", local_address, port);
    let vm_route = env_or("VM_ROUTE", "vm");
    let vm_upstream = env_or("VM_UPSTREAM_BASE", "http://127.0.0.1:40051");
    let vm_ws_base = http_base_to_ws(&vm_upstream);
    let vm_prefix = format!("/{}", vm_route);

    // Route slugs
    let fhir_route = env_or("FHIR_ROUTE", "fhir_server");
    let terminology_route = env_or("TERMINOLOGY_ROUTE", "terminology_server");
    let validation_route = env_or("VALIDATION_ROUTE", "validation_server");
    let openfda_route = env_or("OPENFDA_ROUTE", "openfda_api");
    let mcp_route = env_or("MCP_ROUTE", "mcp_server");
    let sandbox_route = env_or("SANDBOX_ROUTE", "sandbox_server");

    // Upstream bases (container-internal)
    let fhir_upstream = env_or("FHIR_UPSTREAM_BASE", "http://hapi:8080");
    let terminology_upstream = env_or("TERMINOLOGY_UPSTREAM_BASE", "http://tx.fhir.org/r4");
    let validation_upstream = env_or("VALIDATION_UPSTREAM_BASE", "http://validator:3500");
    let openfda_upstream = env_or("OPENFDA_UPSTREAM_BASE", "https://api.fda.gov");
    let mcp_upstream = env_or("MCP_UPSTREAM_BASE", "http://mcp:8000/mcp");
    let sandbox_upstream = env_or("SANDBOX_UPSTREAM_BASE", "http://sandbox:8088");

    let client = Arc::new(
        reqwest::ClientBuilder::new()
            .timeout(Duration::from_secs(300)) // TODO: super long timeout to support large synthea uploads, also reduced work
            .connect_timeout(Duration::from_secs(5)) // fast fail on TCP connect
            .pool_max_idle_per_host(64) // keep pooled conns
            .tcp_keepalive(Some(Duration::from_secs(30))) // avoid idle drops
            .build()
            .expect("failed building reqwest client"),
    );

    // Docs + health
    let api_service =
        OpenApiService::new(Api, "Middleman", "1.0").server(&format!("http://{}/api", bind_addr));
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

    let c7 = client.clone();
    let vm_http_handler = make(move |req| {
        let c = c7.clone();
        let upstream = vm_upstream.clone();
        let prefix = vm_prefix.clone();
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
                .at(&format!("/{vm_route}/*"), vm_http_handler)
                .at(&format!("/{vm_route}/sessions/:id/tty"), get(vm_tty_ws))
                .data(vm_ws_base.clone()),
        )
        .await
}
