//! Include the CLI dispatcher when opening an unloaded thread from the command center.

use super::focus_palette::PtyCodex;
use super::focus_palette::write_test_config;
use anyhow::Context;
use anyhow::Result;
use codex_app_server_protocol::JSONRPCMessage;
use futures::SinkExt;
use futures::StreamExt;
use pretty_assertions::assert_eq;
use serde_json::json;
use std::sync::Arc;
use std::sync::atomic::AtomicUsize;
use std::sync::atomic::Ordering;
use std::time::Duration;
use tokio::net::UnixListener;
use tokio::task::JoinSet;
use tokio_tungstenite::tungstenite::Message;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn agents_opens_unloaded_history_on_the_cli_stack() -> Result<()> {
    let cwd = codex_utils_cargo_bin::repo_root()?;
    let home = tempfile::tempdir_in("/tmp")?;
    write_test_config(home.path(), &cwd)?;
    let path = home.path().join("config.toml");
    let config = std::fs::read_to_string(&path)?;
    std::fs::write(
        path,
        format!("{config}\n[tui]\nstatus_line = [\"thread-id\"]\n"),
    )?;
    let socket_path = codex_app_server_client::app_server_control_socket_path(home.path())?;
    std::fs::create_dir_all(socket_path.parent().unwrap())?;
    let listener = UnixListener::bind(socket_path.as_path())?;
    let resumed = Arc::new(AtomicUsize::new(0));
    let server_resumed = Arc::clone(&resumed);
    let server_cwd = cwd.clone();
    let id = "00000000-0000-0000-0000-000000000042";
    let mut servers = JoinSet::new();
    servers.spawn(async move {
        let mut connections = JoinSet::new();
        loop {
            let (stream, _) = listener.accept().await?;
            let cwd = server_cwd.clone();
            let resumed = Arc::clone(&server_resumed);
            connections.spawn(async move {
                // Startup also probes the socket without a WebSocket handshake.
                let Ok(mut socket) = tokio_tungstenite::accept_async(stream).await else {
                    return Ok::<_, anyhow::Error>(());
                };
                while let Some(Ok(Message::Text(text))) = socket.next().await {
                    let JSONRPCMessage::Request(request) = serde_json::from_str(&text)? else {
                        continue;
                    };
                    let thread = json!({
                        "id": id, "sessionId": id, "name": "Stack history",
                        "preview": "Stack history", "ephemeral": false,
                        "modelProvider": "openai", "createdAt": 1, "updatedAt": 2,
                        "status": {"type": "notLoaded"}, "cwd": cwd,
                        "cliVersion": "0.0.0", "source": "cli", "turns": []
                    });
                    let turn = json!({"id": "saved", "status": "completed", "error": null,
                        "items": [{"id": "answer", "type": "agentMessage",
                        "text": "AGENTS_STACK_RESTORED_HISTORY"}]});
                    let result = match request.method.as_str() {
                        "initialize" => json!({"userAgent": "agents-stack-test"}),
                        "experimentalFeature/list" => json!({"data":
                            (["code_mode_host", "auth_elicitation"].map(|name| json!({
                                "name": name, "stage": "stable", "displayName": null,
                                "description": null, "announcement": null,
                                "enabled": true, "defaultEnabled": true
                            }))), "nextCursor": null}),
                        "account/read" => json!({"account": {"type": "apiKey"}, "requiresOpenaiAuth": false}),
                        "model/list" => json!({"data": [], "nextCursor": null}),
                        "config/read" => json!({"config": {"model": "gpt-5.6-terra",
                            "model_provider": "openai", "tui": {"status_line": ["thread-id"]},
                            "projects": {cwd.to_string_lossy(): {"trust_level": "trusted"}}},
                            "origins": {}, "layers": []}),
                        "configRequirements/read" => json!({"requirements": null}),
                        "thread/list" => json!({"data": [thread], "nextCursor": null}),
                        "thread/loaded/list" => json!({"data": [], "nextCursor": null}),
                        "thread/read" => json!({"thread": thread}),
                        "thread/turns/list" => json!({"data": [turn], "nextCursor": null}),
                        "thread/resume" => {
                            resumed.fetch_add(1, Ordering::SeqCst);
                            let mut thread = thread;
                            thread["status"] = json!({"type": "idle"});
                            thread["turns"] = json!([turn]);
                            json!({"thread": thread, "model": "gpt-5.6-terra", "modelProvider": "openai",
                                "cwd": cwd, "approvalPolicy": "never", "approvalsReviewer": "user",
                                "sandbox": {"type": "readOnly"}, "reasoningEffort": null})
                        }
                        "thread/goal/get" => json!({"goal": null}),
                        "skills/list" => json!({"data": []}),
                        _ => {
                            socket.send(Message::Text(json!({"id": request.id,
                                "error": {"code": -32601, "message": "method not found"}})
                                .to_string().into())).await?;
                            continue;
                        }
                    };
                    socket.send(Message::Text(json!({"id": request.id, "result": result})
                        .to_string().into())).await?;
                }
                Ok(())
            });
        }
        #[allow(unreachable_code)]
        Ok::<_, anyhow::Error>(())
    });

    // Do not fall back to codex-tui: that omits the CLI frames involved in the crash.
    let endpoint = format!("unix://{}", socket_path.display());
    let mut terminal = PtyCodex::start_cli(&cwd, home, &["agents", "--remote", &endpoint])?;
    terminal
        .wait_for_screen("Stack history")
        .with_context(|| terminal.screen_contents())?;
    terminal.ensure_running()?;
    terminal.write_input(b"\r")?;
    terminal.wait_for_screen(id)?;
    terminal.wait_for_screen("AGENTS_STACK_RESTORED_HISTORY")?;
    terminal.wait_for_screen("Ask Codex to do anything")?;
    terminal.write_input(b"unsent-stack-probe")?;
    terminal.wait_for_screen("unsent-stack-probe")?;
    terminal.read_output(Duration::from_millis(/*millis*/ 250))?;
    terminal.ensure_running()?;
    assert_eq!(resumed.load(Ordering::SeqCst), 1);
    Ok(())
}
