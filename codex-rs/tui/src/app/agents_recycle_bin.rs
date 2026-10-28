//! The bin is a restore picker; permanent removal is handled by server-side expiry.

use super::App;
use crate::app_event::AppEvent;
use crate::app_server_session::AppServerSession;
use crate::bottom_pane::SelectionItem;
use crate::bottom_pane::SelectionViewParams;
use codex_protocol::ThreadId;

impl App {
    pub(super) async fn open_agents_bin(&mut self, server: &mut AppServerSession) {
        let result = server.trashed_threads().await;
        let items = match result {
            Ok(threads) => threads
                .into_iter()
                .filter_map(|thread| {
                    let id = ThreadId::from_string(&thread.id).ok()?;
                    Some(SelectionItem {
                        name: thread.name.unwrap_or(thread.preview),
                        description: Some(thread.cwd.display().to_string()),
                        actions: vec![Box::new(move |tx| {
                            tx.send(AppEvent::RestoreAgentsBin { thread_id: id })
                        })],
                        dismiss_on_select: true,
                        ..Default::default()
                    })
                })
                .collect(),
            Err(error) => vec![SelectionItem {
                name: format!("Could not open bin: {error}"),
                dismiss_on_select: true,
                ..Default::default()
            }],
        };
        self.chat_widget.show_selection_view(SelectionViewParams {
            title: Some("Bin · 30 days · Enter to restore".into()),
            items,
            ..SelectionViewParams::picker()
        });
    }

    pub(super) async fn restore_agents_bin(&mut self, server: &mut AppServerSession, id: ThreadId) {
        match server.restore_trashed_thread(id).await {
            Ok(_) => self.refresh_agents_overview_threads(server),
            Err(error) => self
                .chat_widget
                .add_error_message(format!("Could not restore task: {error}")),
        }
    }
}
