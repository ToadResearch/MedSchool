use crate::vm::{create_session, VmSession};

use std::{collections::HashMap, time::Duration};

use bollard::{exec::{CreateExecOptions, StartExecResults}, query_parameters::{CreateContainerOptions, InspectContainerOptions, InspectNetworkOptions, RemoveContainerOptions, StartContainerOptions, StopContainerOptions}, secret::{ContainerCreateBody, EndpointSettings, HostConfig}};
use futures_util::TryStreamExt;
use poem::{http::StatusCode, web::{Data, Path}};
use poem_openapi::{payload::{Json, PlainText}, Object, OpenApi};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{AppState, CreateSessionRequest, SessionCreated, SessionInfo, vm::{ExecRequest, ExecResponse, ExecResult}};

pub struct Session {
    pub session_id: String,
    pub vm: SessionInfo,
    pub running: bool,
    pub agent_id: String,
    pub fhir_queries: Vec<FhirQuery>,
    pub tool_calls: Vec<ToolCall>,
    pub terminal_commands: Vec<String>,
    pub question: String,
    pub answer: String,
    pub with_tools: bool,
    pub optimal_num_steps: Option<i32>,
}

#[derive(Serialize, Clone)]
pub struct FhirQuery {
    pub method: String,
    pub url: String,
    pub error: Option<String>,
}

#[derive(Serialize, Clone)]
pub struct ToolCall {
    pub tool_name: String,
    pub input: String,
    pub output: String,
    pub error: Option<String>,
}

#[derive(Object)]
pub struct CreateSessionResponse {
    pub session_id: String,
}

pub struct SessionApi;

#[OpenApi]
impl SessionApi {
    #[oai(path = "/", method = "post")]
    async fn create_session(
        &self,
        state: Data<&AppState>,
        req: Json<CreateSessionRequest>,
    ) -> poem::Result<Json<CreateSessionResponse>> {
        let body = req.0;
        let session_info = create_session(&state, body.clone()).await.map_err(|e| {
            poem::Error::from_string(
                format!("Failed to create session: {}", e),
                StatusCode::INTERNAL_SERVER_ERROR,
            )
        })?;
        let mut sessions = state.sessions.write().await;
        sessions.insert(
            session_info.id.to_string(),
            Session {
                session_id: session_info.id.to_string(),
                vm: session_info.clone(),
                running: false,
                agent_id: "not implemented yet".into(), // TODO: fix this
                fhir_queries: Vec::new(),
                tool_calls: Vec::new(),
                terminal_commands: Vec::new(),
                question: String::new(),
                answer: String::new(),
                with_tools: body.with_tools.unwrap_or(false),
                optimal_num_steps: None, // TODO: fix this
            },
        );
        Ok(Json(CreateSessionResponse { session_id: session_info.id.to_string() }))
    }

    #[oai(path = "/:id", method = "get")]
    async fn get_session(
        &self,
        state: Data<&AppState>,
        Path(id): Path<String>,
    ) -> poem::Result<Json<serde_json::Value>> {
        let sessions = state.sessions.read().await;
        let session = sessions.get(&id).ok_or_else(|| {
            poem::Error::from_string(
                format!("Session {} not found", id),
                StatusCode::NOT_FOUND,
            )
        })?;
        Ok(Json(serde_json::json!({
            "session_id": session.session_id.clone(),
            "vm": session.vm.clone(),
            "running": session.running,
            "agent_id": session.agent_id.clone(),
            "fhir_queries": session.fhir_queries.clone(),
            "tool_calls": session.tool_calls.clone(),
            "terminal_commands": session.terminal_commands.clone(),
            "question": session.question.clone(),
            "answer": session.answer.clone(),
            "with_tools": session.with_tools,
            "optimal_num_steps": session.optimal_num_steps,
        })))
    }
}
