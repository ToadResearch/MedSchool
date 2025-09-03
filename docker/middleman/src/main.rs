use poem::{listener::TcpListener, Route, Server, Response, http::StatusCode, Request, endpoint::make};
use poem_openapi::{param::Query, payload::PlainText, OpenApi, OpenApiService};
use reqwest::Client;
use std::env;

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
}

async fn forward_request(req: Request, target_url: &str, strip_prefix: &str) -> Response {
    let client = Client::new();
    let full_path = req.uri().path_and_query().map(|pq| pq.as_str().to_string()).unwrap_or(req.uri().path().to_string());
    let path = if full_path.starts_with(strip_prefix) {
        &full_path[strip_prefix.len()..]
    } else {
        &full_path
    };
    let url = format!("{}{}", target_url, path);
    let method = req.method().clone();
    let mut request_builder = client.request(method.clone(), &url);

    // Copy headers, excluding host and connection
    for (key, value) in req.headers() {
        if key != "host" && key != "connection" {
            request_builder = request_builder.header(key, value.clone());
        }
    }

    // Add body if present
    let body_bytes = req.into_body().into_bytes().await.unwrap_or_default();
    if !body_bytes.is_empty() {
        request_builder = request_builder.body(body_bytes);
    }

    println!("Forwarding {} {} to {}", method, full_path, url);

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
            eprintln!("Error forwarding request: {}", e);
            Response::builder().status(StatusCode::INTERNAL_SERVER_ERROR).body("Internal Server Error")
        }
    }
}

async fn forward_to_fhir(req: Request) -> Response {
    forward_request(req, "http://hapi:8080", "/fhir_server").await
}

async fn forward_to_terminology(req: Request) -> Response {
    forward_request(req, "http://hapi:8080", "/terminology_server").await
}

#[tokio::main]
async fn main() -> Result<(), std::io::Error> {
    tracing_subscriber::fmt::init();

    let port = env::var("MIDDLEMAN_PORT").unwrap_or_else(|_| "3000".to_string());
    let bind_addr = format!("0.0.0.0:{}", port);

    let api_service =
        OpenApiService::new(Api, "Hello World", "1.0").server(&format!("http://localhost:{}/api", port));
    let ui = api_service.swagger_ui();

    Server::new(TcpListener::bind(&bind_addr))
        .run(Route::new()
            .nest("/api", api_service)
            .nest("/docs", ui)
            .at("/fhir_server/*", make(forward_to_fhir))
            .at("/terminology_server/*", make(forward_to_terminology))
        )
        .await
}