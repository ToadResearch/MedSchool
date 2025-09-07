// use std::sync::Arc;

// use poem::{endpoint::make, Route, Request, Response};
// use reqwest::Client;

// // Simple env reader (kept here so this module stays self-contained)
// fn env_or(key: &str, default: &str) -> String {
//     std::env::var(key).unwrap_or_else(|_| default.to_string())
// }

// pub struct ProxiesEnv {
//     pub fhir_route: String,
//     pub terminology_route: String,
//     pub validation_route: String,
//     pub openfda_route: String,
//     pub mcp_route: String,
//     pub sandbox_route: String,

//     pub fhir_upstream: String,
//     pub terminology_upstream: String,
//     pub validation_upstream: String,
//     pub openfda_upstream: String,
//     pub mcp_upstream: String,
//     pub sandbox_upstream: String,
// }
// impl ProxiesEnv {
//     pub fn from_env() -> Self {
//         Self {
//             fhir_route: env_or("FHIR_ROUTE", "fhir_server"),
//             terminology_route: env_or("TERMINOLOGY_ROUTE", "terminology_server"),
//             validation_route: env_or("VALIDATION_ROUTE", "validation_server"),
//             openfda_route: env_or("OPENFDA_ROUTE", "openfda_api"),
//             mcp_route: env_or("MCP_ROUTE", "mcp_server"),
//             sandbox_route: env_or("SANDBOX_ROUTE", "sandbox_server"),

//             fhir_upstream: env_or("FHIR_UPSTREAM_BASE", "http://hapi:8080"),
//             terminology_upstream: env_or("TERMINOLOGY_UPSTREAM_BASE", "http://tx.fhir.org/r4"),
//             validation_upstream: env_or("VALIDATION_UPSTREAM_BASE", "http://validator:3500"),
//             openfda_upstream: env_or("OPENFDA_UPSTREAM_BASE", "https://api.fda.gov"),
//             mcp_upstream: env_or("MCP_UPSTREAM_BASE", "http://mcp:8000/mcp"),
//             sandbox_upstream: env_or("SANDBOX_UPSTREAM_BASE", "http://sandbox:8088"),
//         }
//     }
// }

// pub async fn forward_request(
//     req: Request,
//     client: &Client,
//     target_base: &str,
//     strip_prefix: &str,
// ) -> Response {
//     use poem::http::StatusCode;

//     let full_path = req
//         .uri()
//         .path_and_query()
//         .map(|pq| pq.as_str().to_string())
//         .unwrap_or_else(|| req.uri().path().to_string());

//     let path_after_prefix = if full_path.starts_with(strip_prefix) {
//         &full_path[strip_prefix.len()..]
//     } else {
//         full_path.as_str()
//     };

//     let url = format!("{target_base}{path_after_prefix}");
//     let method = req.method().clone();
//     let mut request_builder = client.request(method.clone(), &url);

//     for (key, value) in req.headers() {
//         let k = key.as_str();
//         if k.eq_ignore_ascii_case("host") || k.eq_ignore_ascii_case("connection") {
//             continue;
//         }
//         request_builder = request_builder.header(key, value);
//     }

//     if let Some(host_val) = req.headers().get("host") {
//         request_builder = request_builder.header("x-forwarded-host", host_val.clone());
//     }

//     let body_bytes = req.into_body().into_bytes().await.unwrap_or_default();
//     if !body_bytes.is_empty() {
//         request_builder = request_builder.body(body_bytes);
//     }

//     println!("Forwarding {} {} -> {}", method, path_after_prefix, url);

//     match request_builder.send().await {
//         Ok(resp) => {
//             let status = resp.status();
//             let mut response_builder = poem::Response::builder().status(status);
//             for (key, value) in resp.headers() {
//                 response_builder = response_builder.header(key, value.clone());
//             }
//             let body = resp.bytes().await.unwrap_or_default();
//             response_builder.body(body)
//         }
//         Err(e) => {
//             eprintln!("Error forwarding request to {url}: {e}");
//             poem::Response::builder()
//                 .status(StatusCode::BAD_GATEWAY)
//                 .body("Upstream Error")
//         }
//     }
// }

// pub fn build_proxy_routes(client: Arc<Client>, p: &ProxiesEnv) -> Route {
//     let fhir_prefix = format!("/{}", p.fhir_route);
//     let terminology_prefix = format!("/{}", p.terminology_route);
//     let validation_prefix = format!("/{}", p.validation_route);
//     let openfda_prefix = format!("/{}", p.openfda_route);
//     let mcp_prefix = format!("/{}", p.mcp_route);
//     let sandbox_prefix = format!("/{}", p.sandbox_route);

//     let c1 = client.clone();
//     let fhir_handler = make(move |req| {
//         let c = c1.clone();
//         let upstream = p.fhir_upstream.clone();
//         let prefix = fhir_prefix.clone();
//         async move { forward_request(req, &c, &upstream, &prefix).await }
//     });

//     let c2 = client.clone();
//     let terminology_handler = make(move |req| {
//         let c = c2.clone();
//         let upstream = p.terminology_upstream.clone();
//         let prefix = terminology_prefix.clone();
//         async move { forward_request(req, &c, &upstream, &prefix).await }
//     });

//     let c3 = client.clone();
//     let validation_handler = make(move |req| {
//         let c = c3.clone();
//         let upstream = p.validation_upstream.clone();
//         let prefix = validation_prefix.clone();
//         async move { forward_request(req, &c, &upstream, &prefix).await }
//     });

//     let c4 = client.clone();
//     let openfda_handler = make(move |req| {
//         let c = c4.clone();
//         let upstream = p.openfda_upstream.clone();
//         let prefix = openfda_prefix.clone();
//         async move { forward_request(req, &c, &upstream, &prefix).await }
//     });

//     let c5 = client.clone();
//     let mcp_handler = make(move |req| {
//         let c = c5.clone();
//         let upstream = p.mcp_upstream.clone();
//         let prefix = mcp_prefix.clone();
//         async move { forward_request(req, &c, &upstream, &prefix).await }
//     });

//     let c6 = client.clone();
//     let sandbox_handler = make(move |req| {
//         let c = c6.clone();
//         let upstream = p.sandbox_upstream.clone();
//         let prefix = sandbox_prefix.clone();
//         async move { forward_request(req, &c, &upstream, &prefix).await }
//     });

//     Route::new()
//         .at(&format!("/{}/{}", p.fhir_route, "*"), fhir_handler)
//         .at(&format!("/{}/{}", p.terminology_route, "*"), terminology_handler)
//         .at(&format!("/{}/{}", p.validation_route, "*"), validation_handler)
//         .at(&format!("/{}/{}", p.openfda_route, "*"), openfda_handler)
//         .at(&format!("/{}/{}", p.mcp_route, "*"), mcp_handler)
//         .at(&format!("/{}/{}", p.sandbox_route, "*"), sandbox_handler)
// }

// // ---- Minimal OpenAPI: hello/health ----
// use poem_openapi::{payload::PlainText, OpenApi};

// pub struct CoreApi;

// #[OpenApi]
// impl CoreApi {
//     #[oai(path = "/hello", method = "get")]
//     async fn index(&self, name: poem_openapi::param::Query<Option<String>>) -> PlainText<String> {
//         match name.0 {
//             Some(name) => PlainText(format!("hello, {name}!")),
//             None => PlainText("hello!".to_string()),
//         }
//     }

//     #[oai(path = "/health", method = "get")]
//     async fn health(&self) -> PlainText<String> {
//         PlainText("OK".to_string())
//     }
// }
