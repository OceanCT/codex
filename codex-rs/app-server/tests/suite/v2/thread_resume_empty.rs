use anyhow::Result;
use app_test_support::MockResponsesConfig;
use app_test_support::TestAppServer;
use app_test_support::create_mock_responses_server_repeating_assistant;
use codex_app_server_protocol::AskForApproval;
use codex_app_server_protocol::ClientRequest;
use codex_app_server_protocol::RequestId;
use codex_app_server_protocol::ThreadHistoryMode;
use codex_app_server_protocol::ThreadResumeParams;
use codex_app_server_protocol::ThreadResumeResponse;
use codex_app_server_protocol::ThreadStartParams;
use codex_app_server_protocol::ThreadStartResponse;
use codex_app_server_protocol::ThreadUnsubscribeParams;
use codex_app_server_protocol::ThreadUnsubscribeResponse;
use pretty_assertions::assert_eq;
use std::time::Duration;
use tempfile::TempDir;
use tokio::time::timeout;

// Match the reported state: the command center has indexed an empty live thread,
// but its deferred rollout has not been created yet.
async fn index_empty_thread(home: &std::path::Path, started: &ThreadStartResponse) -> Result<()> {
    let state = codex_state::StateRuntime::init(
        codex_state::SqliteConfig::new_for_testing(
            codex_utils_absolute_path::AbsolutePathBuf::try_from(home)?,
        ),
        started.model_provider.clone(),
    )
    .await?;
    let mut builder = codex_state::ThreadMetadataBuilder::new(
        codex_protocol::ThreadId::from_string(&started.thread.id)?,
        started.thread.path.clone().expect("rollout path"),
        chrono::Utc::now(),
        codex_protocol::protocol::SessionSource::VSCode,
    );
    builder.cwd = started.cwd.as_path().to_path_buf();
    builder.history_mode = match started.thread.history_mode {
        ThreadHistoryMode::Legacy => codex_protocol::protocol::ThreadHistoryMode::Legacy,
        ThreadHistoryMode::Paginated => codex_protocol::protocol::ThreadHistoryMode::Paginated,
    };
    state
        .upsert_thread(&builder.build(&started.model_provider))
        .await?;
    Ok(())
}

#[tokio::test]
async fn empty_thread_resume_with_overrides_persists_before_rebuild() -> Result<()> {
    for history_mode in [ThreadHistoryMode::Legacy, ThreadHistoryMode::Paginated] {
        let mock = create_mock_responses_server_repeating_assistant("Done").await;
        let home = TempDir::new()?;
        MockResponsesConfig::new(&mock.uri())
            .with_root_config("thread_unload_delay_secs = 3600")
            .write(home.path())?;
        let mut server = TestAppServer::builder()
            .with_codex_home(home.path())
            .build_initialized()
            .await?;
        let started = server
            .start_thread(ThreadStartParams {
                history_mode: Some(history_mode),
                approval_policy: Some(AskForApproval::Never),
                ..Default::default()
            })
            .await?;
        index_empty_thread(home.path(), &started).await?;
        let rollout = started.thread.path.expect("rollout path");
        assert!(
            !rollout.exists(),
            "new empty thread should still be deferred"
        );
        let thread_id = started.thread.id;
        let _: ThreadUnsubscribeResponse = server
            .request(|request_id| ClientRequest::ThreadUnsubscribe {
                request_id,
                params: ThreadUnsubscribeParams {
                    thread_id: thread_id.clone(),
                },
            })
            .await?;
        let resumed: ThreadResumeResponse = server
            .request(|request_id| ClientRequest::ThreadResume {
                request_id,
                params: ThreadResumeParams {
                    thread_id: thread_id.clone(),
                    approval_policy: Some(AskForApproval::OnRequest),
                    exclude_turns: true,
                    ..Default::default()
                },
            })
            .await?;
        assert_eq!(resumed.thread.id, thread_id);
        assert_eq!(resumed.approval_policy, AskForApproval::OnRequest);
        assert!(resumed.thread.turns.is_empty());
        assert!(rollout.is_file(), "rebuild must leave a resumable rollout");
    }
    Ok(())
}

#[tokio::test]
async fn empty_thread_resume_persistence_failure_keeps_live_thread() -> Result<()> {
    let mock = create_mock_responses_server_repeating_assistant("Done").await;
    let home = TempDir::new()?;
    MockResponsesConfig::new(&mock.uri())
        .with_root_config("thread_unload_delay_secs = 3600")
        .write(home.path())?;
    let mut server = TestAppServer::builder()
        .with_codex_home(home.path())
        .build_initialized()
        .await?;
    let started = server
        .start_thread(ThreadStartParams {
            history_mode: Some(ThreadHistoryMode::Paginated),
            approval_policy: Some(AskForApproval::Never),
            ..Default::default()
        })
        .await?;
    index_empty_thread(home.path(), &started).await?;
    let rollout = started.thread.path.expect("rollout path");
    assert!(!rollout.exists());
    let thread_id = started.thread.id;
    let _: ThreadUnsubscribeResponse = server
        .request(|request_id| ClientRequest::ThreadUnsubscribe {
            request_id,
            params: ThreadUnsubscribeParams {
                thread_id: thread_id.clone(),
            },
        })
        .await?;

    // A directory at the file path forces a real storage error on every platform.
    std::fs::create_dir_all(&rollout)?;
    let resume_id = server
        .send_thread_resume_request(ThreadResumeParams {
            thread_id: thread_id.clone(),
            approval_policy: Some(AskForApproval::OnRequest),
            exclude_turns: true,
            ..Default::default()
        })
        .await?;
    let error = timeout(
        Duration::from_secs(10),
        server.read_stream_until_error_message(RequestId::Integer(resume_id)),
    )
    .await??;
    assert_eq!(error.error.code, -32603);
    std::fs::remove_dir(&rollout)?;

    // Recovery requires the original in-memory state, since no rollout exists yet.
    let resumed: ThreadResumeResponse = server
        .request(|request_id| ClientRequest::ThreadResume {
            request_id,
            params: ThreadResumeParams {
                thread_id: thread_id.clone(),
                approval_policy: Some(AskForApproval::OnRequest),
                exclude_turns: true,
                ..Default::default()
            },
        })
        .await?;
    assert_eq!(resumed.thread.id, thread_id);
    assert_eq!(resumed.approval_policy, AskForApproval::OnRequest);
    assert!(rollout.is_file());
    Ok(())
}
