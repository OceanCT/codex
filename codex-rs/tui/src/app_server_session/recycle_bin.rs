//! Typed requests for the Codex Y bin; all mutations stay on the server.

use super::*;

impl AppServerSession {
    pub(crate) async fn trashed_threads(&mut self) -> Result<Vec<Thread>> {
        let mut threads = Vec::new();
        let mut cursor = None;
        loop {
            let request_id = self.next_request_id();
            let params = serde_json::from_value(serde_json::json!({
                "cursor": cursor, "limit": 100, "modelProviders": [],
                "sourceKinds": ["cli", "vscode", "exec", "appServer", "subAgent", "unknown"],
            }))?;
            let page: ThreadListResponse = self
                .client
                .request_typed(ClientRequest::ThreadTrashList { request_id, params })
                .await?;
            threads.extend(page.data);
            cursor = page.next_cursor;
            if cursor.is_none() {
                return Ok(threads);
            }
        }
    }

    pub(crate) async fn restore_trashed_thread(&mut self, id: ThreadId) -> Result<Thread> {
        let request_id = self.next_request_id();
        let response: ThreadUnarchiveResponse = self
            .client
            .request_typed(ClientRequest::ThreadTrashRestore {
                request_id,
                params: ThreadUnarchiveParams {
                    thread_id: id.to_string(),
                },
            })
            .await?;
        Ok(response.thread)
    }
}
