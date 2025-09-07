use crate::{vm::{exec, ExecRequest, ExecResult}, AppState};

use poem::web::Data;
use poem_openapi::{payload::{Json, PlainText}, OpenApi};

pub struct TerminalApi;

#[OpenApi]
impl TerminalApi {
    #[oai(path = "/", method = "post")]
    async fn execute_command(
        &self,
        state: Data<&AppState>,
        request: &poem::Request,
        body: Json<ExecRequest>,
    ) -> ExecResult {
        let session_id = match request.header("x-session-id") {
            Some(s) => s.to_string(),
            None => return ExecResult::BadRequest(PlainText("missing x-session-id header".into())),
        };

        exec(state, &session_id, body).await
    }
}