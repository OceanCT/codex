//! Recoverable deletion for Codex Y. Ordinary archives never expire.
//! The server serializes lifecycle mutations. Expiry only deletes an unchanged,
//! fully archived subtree; restoring or reusing any member cancels expiry.

use super::*;
use chrono::DateTime;
use chrono::Utc;
use serde::Deserialize;
use serde::Serialize;
use std::io::Write;

const RETENTION_DAYS: i64 = 30;

#[derive(Serialize, Deserialize)]
struct TrashEntry {
    root: ThreadId,
    expires_at: DateTime<Utc>,
    members: Vec<TrashMember>,
    restore_ids: Vec<String>,
}

#[derive(Serialize, Deserialize, PartialEq)]
struct TrashMember {
    id: ThreadId,
    archived_at: Option<DateTime<Utc>>,
    updated_at: DateTime<Utc>,
}

impl ThreadRequestProcessor {
    pub(crate) async fn thread_trash(
        &self,
        request_id: ConnectionRequestId,
        params: ThreadArchiveParams,
    ) -> Result<Option<ClientResponsePayload>, JSONRPCErrorError> {
        let _permit = self.acquire_thread_list_state_permit().await?;
        let root = ThreadId::from_string(&params.thread_id)
            .map_err(|error| invalid_request(error.to_string()))?;
        let directory = self.config.codex_home.join("recycle-bin");
        std::fs::create_dir_all(&directory).map_err(trash_error)?;
        // Establish a writable destination before stopping or archiving anything.
        let mut file = tempfile::NamedTempFile::new_in(&directory).map_err(trash_error)?;
        let (response, restore_ids) = self.thread_archive_response(params).await?;
        let mut members = Vec::new();
        for id in self.state_db_spawn_subtree_thread_ids(root).await? {
            let stored = self
                .thread_store
                .read_thread(StoreReadThreadParams {
                    thread_id: id,
                    include_archived: true,
                    include_history: false,
                })
                .await
                .map_err(trash_error)?;
            members.push(TrashMember {
                id,
                archived_at: stored.archived_at,
                updated_at: stored.updated_at,
            });
        }
        let entry = TrashEntry {
            root,
            expires_at: Utc::now() + chrono::Duration::days(RETENTION_DAYS),
            members,
            restore_ids: restore_ids.clone(),
        };
        serde_json::to_writer(&mut file, &entry).map_err(trash_error)?;
        file.flush().map_err(trash_error)?;
        file.as_file().sync_all().map_err(trash_error)?;
        file.persist(directory.join(format!("{root}.json")))
            .map_err(trash_error)?;
        self.outgoing.send_response(request_id, response).await;
        for thread_id in restore_ids {
            self.outgoing
                .send_server_notification(ServerNotification::ThreadArchived(
                    ThreadArchivedNotification { thread_id },
                ))
                .await;
        }
        Ok(None)
    }

    pub(crate) async fn thread_trash_list(
        &self,
        mut params: ThreadListParams,
    ) -> Result<Option<ClientResponsePayload>, JSONRPCErrorError> {
        self.purge_expired_trash().await;
        params.archived = Some(true);
        let mut response = self.thread_list_response_inner(params).await?;
        // Preserve the underlying cursor, including for empty filtered pages.
        let directory = self.config.codex_home.join("recycle-bin");
        response.data.retain(|thread| {
            ThreadId::from_string(&thread.id)
                .is_ok_and(|id| directory.join(format!("{id}.json")).is_file())
        });
        Ok(Some(response.into()))
    }

    pub(crate) async fn thread_trash_restore(
        &self,
        params: ThreadUnarchiveParams,
    ) -> Result<Option<ClientResponsePayload>, JSONRPCErrorError> {
        let _permit = self.acquire_thread_list_state_permit().await?;
        let root = ThreadId::from_string(&params.thread_id)
            .map_err(|error| invalid_request(error.to_string()))?;
        let path = self
            .config
            .codex_home
            .join("recycle-bin")
            .join(format!("{root}.json"));
        let entry: TrashEntry = serde_json::from_slice(&std::fs::read(&path).map_err(trash_error)?)
            .map_err(trash_error)?;
        if entry.root != root {
            return Err(invalid_request("bin entry does not match thread"));
        }
        // Cancel expiry durably before restoring. Partial failures remain ordinary archives.
        std::fs::remove_file(path).map_err(trash_error)?;
        for thread_id in entry
            .restore_ids
            .into_iter()
            .filter(|id| *id != params.thread_id)
        {
            let (_, thread_id) = self
                .thread_unarchive_response(ThreadUnarchiveParams { thread_id })
                .await?;
            self.outgoing
                .send_server_notification(ServerNotification::ThreadUnarchived(
                    ThreadUnarchivedNotification { thread_id },
                ))
                .await;
        }
        let (response, thread_id) = self.thread_unarchive_response(params).await?;
        self.outgoing
            .send_server_notification(ServerNotification::ThreadUnarchived(
                ThreadUnarchivedNotification { thread_id },
            ))
            .await;
        Ok(Some(response.into()))
    }

    /// Run on dashboard/list refresh, including the first refresh after an offline period.
    pub(super) async fn purge_expired_trash(&self) {
        let Ok(_permit) = self.acquire_thread_list_state_permit().await else {
            return;
        };
        let directory = self.config.codex_home.join("recycle-bin");
        let Ok(files) = std::fs::read_dir(directory) else {
            return;
        };
        for file in files.flatten() {
            let path = file.path();
            if path.extension().is_none_or(|extension| extension != "json") {
                continue;
            }
            let result = async {
                let entry: TrashEntry =
                    serde_json::from_slice(&std::fs::read(&path).map_err(trash_error)?)
                        .map_err(trash_error)?;
                if path.file_stem().and_then(|stem| stem.to_str())
                    != Some(entry.root.to_string().as_str())
                    || entry.members.is_empty()
                    || entry.expires_at > Utc::now()
                {
                    return Ok::<(), JSONRPCErrorError>(());
                }
                let ids = self.state_db_spawn_subtree_thread_ids(entry.root).await?;
                let expected: std::collections::HashSet<_> =
                    entry.members.iter().map(|member| member.id).collect();
                let mut unchanged =
                    ids.into_iter().collect::<std::collections::HashSet<_>>() == expected;
                for member in &entry.members {
                    if self.thread_manager.get_thread(member.id).await.is_ok() {
                        unchanged = false;
                        break;
                    }
                    let stored = self
                        .thread_store
                        .read_thread(StoreReadThreadParams {
                            thread_id: member.id,
                            include_archived: true,
                            include_history: false,
                        })
                        .await
                        .map_err(trash_error)?;
                    unchanged &= member.archived_at.is_some()
                        && member.archived_at == stored.archived_at
                        && member.updated_at == stored.updated_at;
                }
                if unchanged {
                    let mut deleted = Vec::new();
                    self.thread_delete_response(
                        ThreadDeleteParams {
                            thread_id: entry.root.to_string(),
                        },
                        &mut deleted,
                    )
                    .await?;
                    self.send_thread_deleted_notifications(deleted).await;
                }
                // Reused/restored threads lose their expiry instead of risking their new work.
                std::fs::remove_file(path).map_err(trash_error)?;
                Ok(())
            }
            .await;
            if let Err(error) = result {
                tracing::warn!(?error, "bin cleanup deferred");
            }
        }
    }
}

fn trash_error(error: impl std::fmt::Display) -> JSONRPCErrorError {
    internal_error(format!("bin operation failed: {error}"))
}
