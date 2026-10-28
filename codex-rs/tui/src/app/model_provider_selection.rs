//! Resume an idle thread with the selected provider before updating the visible model.

use super::App;
use super::session_lifecycle::ThreadAttachPresentation;
use crate::app_server_session::AppServerSession;
use crate::app_server_session::ResumeModelSettings;
use crate::chatwidget::ThreadInputStateRestoreMode;
use crate::tui;
use codex_protocol::openai_models::ReasoningEffort;
use color_eyre::eyre::Result;

impl App {
    pub(super) async fn select_provider_model(
        &mut self,
        tui: &mut tui::Tui,
        app_server: &mut AppServerSession,
        model: String,
        effort: Option<ReasoningEffort>,
    ) {
        if let Err(error) = self
            .apply_provider_model(tui, app_server, model, effort)
            .await
        {
            self.chat_widget
                .add_error_message(format!("Could not switch model provider: {error}"));
        }
    }

    async fn apply_provider_model(
        &mut self,
        tui: &mut tui::Tui,
        app_server: &mut AppServerSession,
        model: String,
        effort: Option<ReasoningEffort>,
    ) -> Result<()> {
        if self.chat_widget.is_user_turn_pending_or_running() {
            color_eyre::eyre::bail!(
                "Wait for the current turn to finish before switching providers"
            );
        }
        if self.app_server_target.uses_remote_workspace() {
            color_eyre::eyre::bail!("Local model profiles cannot be used on a remote workspace");
        }
        let Some(thread_id) = self.active_thread_id else {
            return Ok(());
        };
        let profiles = crate::model_picker_profiles::load(self.config.codex_home.as_path())?;
        let profile = profiles
            .into_iter()
            .find(|profile| profile.preset.model == model);
        let mut config = self.config.clone();
        config.model = Some(model.clone());
        config.model_reasoning_effort = effort.clone();
        let (provider, mut overrides) = match profile {
            Some(profile) => {
                config.service_tier = profile
                    .overrides
                    .get("service_tier")
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_string);
                (profile.provider, profile.overrides)
            }
            None => (
                config.model_provider_id.clone(),
                std::collections::HashMap::new(),
            ),
        };
        if let Some(effort) = effort.as_ref() {
            overrides.insert(
                "model_reasoning_effort".to_string(),
                serde_json::to_value(effort)?,
            );
        }
        let input = self.chat_widget.capture_thread_input_state();
        app_server.thread_unsubscribe(thread_id).await?;
        let result = app_server
            .resume_thread_with_model_overrides(
                &self.local_settings,
                config,
                thread_id,
                ResumeModelSettings::OverrideFromCurrentConfig,
                Some((provider.clone(), overrides)),
            )
            .await;
        let mut resumed = match result {
            Ok(resumed) => resumed,
            Err(error) => {
                // Restore the subscription after a rejected provider configuration.
                app_server
                    .resume_thread(
                        &self.local_settings,
                        self.config.clone(),
                        thread_id,
                        ResumeModelSettings::PreserveExistingThread,
                    )
                    .await?;
                return Err(error);
            }
        };
        if resumed.session.model != model || resumed.session.model_provider_id != provider {
            color_eyre::eyre::bail!(
                "The server did not apply the selected provider; wait for the active turn to finish and retry"
            );
        }
        let mut mode = self.chat_widget.effective_collaboration_mode();
        mode.settings.model = model.clone();
        mode.settings.reasoning_effort = effort.clone();
        app_server
            .thread_settings_update(codex_app_server_protocol::ThreadSettingsUpdateParams {
                thread_id: thread_id.to_string(),
                model: Some(model.clone()),
                effort,
                collaboration_mode: Some(mode.clone()),
                ..Default::default()
            })
            .await?;
        resumed.session.collaboration_mode = Some(Box::new(mode.clone()));
        self.replace_chat_widget_with_app_server_thread(
            tui,
            resumed,
            ThreadAttachPresentation::SessionLineage,
            None,
        )
        .await?;
        self.chat_widget.restore_thread_input_state(
            input,
            ThreadInputStateRestoreMode {
                preserve_in_flight_turn: false,
            },
        );
        // Draft restoration also restores the previous collaboration settings.
        self.chat_widget.set_model(&model);
        self.chat_widget.set_effective_collaboration_mode(mode);
        self.chat_widget.add_info_message(
            format!("Model changed to {model} for this session only"),
            None,
        );
        Ok(())
    }
}
