use anyhow::Result;
use app_test_support::MockResponsesConfig;
use app_test_support::TestAppServer;
use app_test_support::create_mock_responses_server_repeating_assistant;
use codex_app_server_protocol::ClientRequest;
use codex_app_server_protocol::RequestId;
use codex_app_server_protocol::ThreadArchiveParams;
use codex_app_server_protocol::ThreadArchiveResponse;
use codex_app_server_protocol::ThreadClosedNotification;
use codex_app_server_protocol::ThreadHistoryMode;
use codex_app_server_protocol::ThreadLoadedListParams;
use codex_app_server_protocol::ThreadLoadedListResponse;
use codex_app_server_protocol::ThreadResumeParams;
use codex_app_server_protocol::ThreadResumeResponse;
use codex_app_server_protocol::ThreadStartParams;
use codex_app_server_protocol::ThreadUnarchiveParams;
use codex_app_server_protocol::ThreadUnarchiveResponse;
use codex_app_server_protocol::ThreadUnsubscribeParams;
use codex_app_server_protocol::ThreadUnsubscribeResponse;
use pretty_assertions::assert_eq;
use std::time::Duration;
use tempfile::TempDir;
use tokio::time::timeout;

#[tokio::test]
async fn empty_thread_trash_restores_after_restart() -> Result<()> {
    for history_mode in [ThreadHistoryMode::Legacy, ThreadHistoryMode::Paginated] {
        let mock = create_mock_responses_server_repeating_assistant("Done").await;
        let home = TempDir::new()?;
        MockResponsesConfig::new(&mock.uri()).write(home.path())?;
        let mut server = TestAppServer::builder()
            .with_codex_home(home.path())
            .build_initialized()
            .await?;
        let started = server
            .start_thread(ThreadStartParams {
                history_mode: Some(history_mode),
                ..Default::default()
            })
            .await?;
        assert!(!started.thread.path.as_ref().unwrap().exists());
        let id = started.thread.id;
        let _: ThreadArchiveResponse = server
            .request(|request_id| ClientRequest::ThreadTrash {
                request_id,
                params: ThreadArchiveParams {
                    thread_id: id.clone(),
                },
            })
            .await?;
        server.shutdown_gracefully().await?;
        let mut server = TestAppServer::builder()
            .with_codex_home(home.path())
            .build_initialized()
            .await?;
        let restored: ThreadUnarchiveResponse = server
            .request(|request_id| ClientRequest::ThreadTrashRestore {
                request_id,
                params: ThreadUnarchiveParams {
                    thread_id: id.clone(),
                },
            })
            .await?;
        assert_eq!(restored.thread.id, id);
        let resumed: ThreadResumeResponse = server
            .request(|request_id| ClientRequest::ThreadResume {
                request_id,
                params: ThreadResumeParams {
                    thread_id: id.clone(),
                    ..Default::default()
                },
            })
            .await?;
        assert_eq!(resumed.thread.id, id);
        assert!(resumed.thread.turns.is_empty());
    }
    Ok(())
}

#[tokio::test]
async fn empty_thread_idle_unload_remains_resumable() -> Result<()> {
    for history_mode in [ThreadHistoryMode::Legacy, ThreadHistoryMode::Paginated] {
        let mock = create_mock_responses_server_repeating_assistant("Done").await;
        let home = TempDir::new()?;
        MockResponsesConfig::new(&mock.uri())
            .with_root_config("thread_unload_delay_secs = 0")
            .write(home.path())?;
        let mut server = TestAppServer::builder()
            .with_codex_home(home.path())
            .build_initialized()
            .await?;
        let started = server
            .start_thread(ThreadStartParams {
                history_mode: Some(history_mode),
                ..Default::default()
            })
            .await?;
        let path = started.thread.path.unwrap();
        assert!(!path.exists());
        let id = started.thread.id;
        let _: ThreadUnsubscribeResponse = server
            .request(|request_id| ClientRequest::ThreadUnsubscribe {
                request_id,
                params: ThreadUnsubscribeParams {
                    thread_id: id.clone(),
                },
            })
            .await?;
        let closed: ThreadClosedNotification = timeout(
            Duration::from_secs(10),
            server.read_notification("thread/closed"),
        )
        .await??;
        assert_eq!(closed.thread_id, id);
        assert!(
            path.is_file(),
            "idle unload must preserve an empty thread's rollout"
        );
        let resumed: ThreadResumeResponse = server
            .request(|request_id| ClientRequest::ThreadResume {
                request_id,
                params: ThreadResumeParams {
                    thread_id: id.clone(),
                    ..Default::default()
                },
            })
            .await?;
        assert_eq!(resumed.thread.id, id);
        assert!(resumed.thread.turns.is_empty());
    }
    Ok(())
}

#[tokio::test]
async fn empty_thread_storage_failure_preserves_runtime_for_retry() -> Result<()> {
    for operation in ["thread/trash", "thread/unsubscribe"] {
        let mock = create_mock_responses_server_repeating_assistant("Done").await;
        let home = TempDir::new()?;
        MockResponsesConfig::new(&mock.uri())
            .with_root_config("thread_unload_delay_secs = 0")
            .write(home.path())?;
        let mut server = TestAppServer::builder()
            .with_codex_home(home.path())
            .build_initialized()
            .await?;
        let started = server.start_thread(ThreadStartParams::default()).await?;
        let path = started.thread.path.unwrap();
        let id = started.thread.id;
        std::fs::create_dir_all(&path)?;
        if operation == "thread/trash" {
            let request_id = server
                .send_request(operation, Some(serde_json::json!({"threadId": id})))
                .await?;
            let error = timeout(
                Duration::from_secs(10),
                server.read_stream_until_error_message(RequestId::Integer(request_id)),
            )
            .await??;
            assert_eq!(error.error.code, -32603);
            assert!(
                !home
                    .path()
                    .join("recycle-bin")
                    .join(format!("{id}.json"))
                    .exists()
            );
        } else {
            let _: ThreadUnsubscribeResponse = server
                .request(|request_id| ClientRequest::ThreadUnsubscribe {
                    request_id,
                    params: ThreadUnsubscribeParams {
                        thread_id: id.clone(),
                    },
                })
                .await?;
            assert!(
                timeout(
                    Duration::from_millis(250),
                    server.read_notification::<ThreadClosedNotification>("thread/closed")
                )
                .await
                .is_err()
            );
        }
        let loaded: ThreadLoadedListResponse = server
            .request(|request_id| ClientRequest::ThreadLoadedList {
                request_id,
                params: ThreadLoadedListParams::default(),
            })
            .await?;
        assert_eq!(loaded.data, vec![id.clone()]);
        std::fs::remove_dir(&path)?;
        if operation == "thread/trash" {
            let _: ThreadArchiveResponse = server
                .request(|request_id| ClientRequest::ThreadTrash {
                    request_id,
                    params: ThreadArchiveParams {
                        thread_id: id.clone(),
                    },
                })
                .await?;
        } else {
            let closed: ThreadClosedNotification = timeout(
                Duration::from_secs(10),
                server.read_notification("thread/closed"),
            )
            .await??;
            assert_eq!(closed.thread_id, id);
            assert!(path.is_file());
        }
    }
    Ok(())
}
