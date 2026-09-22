use anyhow::Result;
use app_test_support::MockResponsesConfig;
use app_test_support::TestAppServer;
use app_test_support::create_fake_rollout;
use app_test_support::create_mock_responses_server_repeating_assistant;
use codex_app_server_protocol::ClientRequest;
use codex_app_server_protocol::ThreadArchiveParams;
use codex_app_server_protocol::ThreadArchiveResponse;
use codex_app_server_protocol::ThreadListResponse;
use codex_app_server_protocol::ThreadUnarchiveParams;
use codex_app_server_protocol::ThreadUnarchiveResponse;
use pretty_assertions::assert_eq;
use serde_json::json;
use tempfile::TempDir;

#[tokio::test]
async fn trash_survives_restart_and_restores_without_affecting_ordinary_archives() -> Result<()> {
    let mock = create_mock_responses_server_repeating_assistant("Done").await;
    let home = TempDir::new()?;
    MockResponsesConfig::new(&mock.uri()).write(home.path())?;
    let mut ids = Vec::new();
    for name in ["trash", "archive", "child"] {
        ids.push(create_fake_rollout(
            home.path(),
            "2025-01-01T00-00-00",
            "2025-01-01T00:00:00Z",
            name,
            Some("mock_provider"),
            /*git_info*/ None,
        )?);
    }
    let mut server = TestAppServer::builder()
        .with_codex_home(home.path())
        .build_initialized()
        .await?;
    let state = codex_state::StateRuntime::init(
        codex_state::SqliteConfig::new_for_testing(
            codex_utils_absolute_path::AbsolutePathBuf::try_from(home.path()).unwrap(),
        ),
        "mock_provider".into(),
    )
    .await?;
    state
        .upsert_thread_spawn_edge(
            codex_protocol::ThreadId::from_string(&ids[0])?,
            codex_protocol::ThreadId::from_string(&ids[2])?,
            codex_state::DirectionalThreadSpawnEdgeStatus::Open,
        )
        .await?;
    let _: ThreadArchiveResponse = server
        .request(|request_id| ClientRequest::ThreadTrash {
            request_id,
            params: ThreadArchiveParams {
                thread_id: ids[0].clone(),
            },
        })
        .await?;
    let _: ThreadArchiveResponse = server
        .request(|request_id| ClientRequest::ThreadArchive {
            request_id,
            params: ThreadArchiveParams {
                thread_id: ids[1].clone(),
            },
        })
        .await?;
    server.shutdown_gracefully().await?;
    let mut server = TestAppServer::builder()
        .with_codex_home(home.path())
        .build_initialized()
        .await?;
    let page: ThreadListResponse = server
        .request(|request_id| ClientRequest::ThreadTrashList {
            request_id,
            params: serde_json::from_value(json!({"modelProviders": []})).unwrap(),
        })
        .await?;
    assert_eq!(
        page.data
            .iter()
            .map(|thread| thread.id.clone())
            .collect::<Vec<_>>(),
        vec![ids[0].clone()]
    );
    let restored: ThreadUnarchiveResponse = server
        .request(|request_id| ClientRequest::ThreadTrashRestore {
            request_id,
            params: ThreadUnarchiveParams {
                thread_id: ids[0].clone(),
            },
        })
        .await?;
    assert_eq!(restored.thread.id, ids[0]);
    let page: ThreadListResponse = server
        .request(|request_id| ClientRequest::ThreadList {
            request_id,
            params: serde_json::from_value(json!({"archived": true, "modelProviders": []}))
                .unwrap(),
        })
        .await?;
    assert_eq!(
        page.data
            .iter()
            .map(|thread| thread.id.clone())
            .collect::<Vec<_>>(),
        vec![ids[1].clone()]
    );
    server.shutdown_gracefully().await?;
    Ok(())
}

#[tokio::test]
async fn expiry_purges_only_unchanged_archives_and_preserves_restored_threads() -> Result<()> {
    for restore in [false, true] {
        let mock = create_mock_responses_server_repeating_assistant("Done").await;
        let home = TempDir::new()?;
        MockResponsesConfig::new(&mock.uri()).write(home.path())?;
        let id = create_fake_rollout(
            home.path(),
            "2025-01-01T00-00-00",
            "2025-01-01T00:00:00Z",
            "expiry",
            Some("mock_provider"),
            /*git_info*/ None,
        )?;
        let mut server = TestAppServer::builder()
            .with_codex_home(home.path())
            .build_initialized()
            .await?;
        let _: ThreadArchiveResponse = server
            .request(|request_id| ClientRequest::ThreadTrash {
                request_id,
                params: ThreadArchiveParams {
                    thread_id: id.clone(),
                },
            })
            .await?;
        if restore {
            let _: ThreadUnarchiveResponse = server
                .request(|request_id| ClientRequest::ThreadUnarchive {
                    request_id,
                    params: ThreadUnarchiveParams {
                        thread_id: id.clone(),
                    },
                })
                .await?;
        }
        let marker = home.path().join("recycle-bin").join(format!("{id}.json"));
        let mut entry: serde_json::Value = serde_json::from_slice(&std::fs::read(&marker)?)?;
        entry["expires_at"] = json!("2020-01-01T00:00:00Z");
        std::fs::write(&marker, serde_json::to_vec(&entry)?)?;
        let page: ThreadListResponse = server
            .request(|request_id| ClientRequest::ThreadList {
                request_id,
                params: serde_json::from_value(json!({"modelProviders": []})).unwrap(),
            })
            .await?;
        assert_eq!(
            page.data
                .iter()
                .map(|thread| thread.id.clone())
                .collect::<Vec<_>>(),
            if restore { vec![id] } else { vec![] }
        );
        let archived: ThreadListResponse = server
            .request(|request_id| ClientRequest::ThreadList {
                request_id,
                params: serde_json::from_value(json!({"archived": true, "modelProviders": []}))
                    .unwrap(),
            })
            .await?;
        assert!(archived.data.is_empty());
        assert!(!marker.exists());
        server.shutdown_gracefully().await?;
    }
    Ok(())
}
