use super::*;
use pretty_assertions::assert_eq;

fn write_picker_profile(home: &std::path::Path) -> Result<()> {
    let mut catalog = codex_models_manager::bundled_models_response()?;
    catalog.models.truncate(1);
    catalog.models[0].slug = "picker-test-model".into();
    catalog.models[0].display_name = "Picker Test Model".into();
    catalog.models[0].visibility = codex_protocol::openai_models::ModelVisibility::List;
    std::fs::write(
        home.join("picker-models.json"),
        serde_json::to_vec(&catalog)?,
    )?;
    std::fs::write(home.join("model-picker-profiles.json"), "[\"picker-test\"]")?;
    std::fs::write(
        home.join("picker-test.config.toml"),
        format!(
            "model = 'picker-test-model'\nmodel_provider = 'picker-test'\nmodel_catalog_json = {:?}\nweb_search = 'disabled'\nservice_tier = 'default'\napproval_policy = 'never'\n[model_providers.picker-test]\nname = 'Picker Test'\nbase_url = 'http://127.0.0.1:1'\nwire_api = 'responses'\nrequires_openai_auth = false\n",
            home.join("picker-models.json").to_string_lossy()
        ),
    )?;
    Ok(())
}

#[tokio::test]
async fn model_picker_profiles_switch_provider_and_restore_original_without_saving_defaults()
-> Result<()> {
    let (mut app, _events, _ops) = make_test_app_with_channels().await;
    write_picker_profile(app.config.codex_home.as_path())?;
    let profiles = crate::model_picker_profiles::load(app.config.codex_home.as_path())?;
    assert!(!profiles[0].overrides.contains_key("approval_policy"));
    let config_path = app.config.codex_home.join("config.toml");
    let before = std::fs::read(&config_path).ok();
    let mut server = Box::pin(crate::start_embedded_app_server_for_picker(&app.config)).await?;
    let started = server.start_thread(&app.config).await?;
    let thread_id = started.session.thread_id;
    let original_model = started.session.model.clone();
    let original_provider = started.session.model_provider_id.clone();
    let original_permissions = started.session.permission_profile.clone();
    app.enqueue_primary_thread_session(started.session, started.turns)
        .await?;
    let mut tui = crate::tui::test_support::make_test_tui()?;
    for (model, provider) in [
        ("picker-test-model", "picker-test"),
        (original_model.as_str(), original_provider.as_str()),
    ] {
        Box::pin(app.handle_event(
            &mut tui,
            &mut server,
            AppEvent::SelectSessionModel {
                model: model.into(),
                effort: Some(ReasoningEffortConfig::High),
            },
        ))
        .await?;
        assert_eq!(app.chat_widget.current_model(), model);
        let observed = server
            .resume_thread(
                &app.local_settings,
                app.config.clone(),
                thread_id,
                crate::app_server_session::ResumeModelSettings::PreserveExistingThread,
            )
            .await?;
        assert_eq!(
            (
                observed.session.model.as_str(),
                observed.session.model_provider_id.as_str()
            ),
            (model, provider)
        );
        assert_eq!(observed.session.permission_profile, original_permissions);
        assert_eq!(observed.session.thread_id, thread_id);
        if provider == "picker-test" {
            server.shutdown().await?;
            server = Box::pin(crate::start_embedded_app_server_for_picker(&app.config)).await?;
            let restored = server
                .resume_thread(
                    &app.local_settings,
                    app.config.clone(),
                    thread_id,
                    crate::app_server_session::ResumeModelSettings::RestoreFromThread,
                )
                .await?;
            assert_eq!(
                (
                    restored.session.model.as_str(),
                    restored.session.model_provider_id.as_str()
                ),
                (model, provider)
            );
        }
    }
    let profile_path = app.config.codex_home.join("picker-test.config.toml");
    let bad_profile = std::fs::read_to_string(&profile_path)?.replace(
        "model_provider = 'picker-test'",
        "model_provider = 'missing-provider'",
    );
    std::fs::write(profile_path, bad_profile)?;
    Box::pin(app.handle_event(
        &mut tui,
        &mut server,
        AppEvent::SelectSessionModel {
            model: "picker-test-model".into(),
            effort: Some(ReasoningEffortConfig::High),
        },
    ))
    .await?;
    assert_eq!(app.chat_widget.current_model(), original_model);
    let retained = server
        .resume_thread(
            &app.local_settings,
            app.config.clone(),
            thread_id,
            crate::app_server_session::ResumeModelSettings::PreserveExistingThread,
        )
        .await?;
    assert_eq!(
        (retained.session.model, retained.session.model_provider_id),
        (original_model, original_provider)
    );
    assert_eq!(std::fs::read(&config_path).ok(), before);
    server.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn model_picker_profiles_display_alongside_existing_models() -> Result<()> {
    let (mut app, _events, _ops) = make_test_app_with_channels().await;
    write_picker_profile(app.config.codex_home.as_path())?;
    let profiles = crate::model_picker_profiles::load(app.config.codex_home.as_path())?;
    let mut models = app.model_catalog.try_list_models()?;
    let count = models.len();
    crate::model_picker_profiles::extend(&mut models, &profiles);
    crate::model_picker_profiles::extend(&mut models, &profiles);
    assert_eq!(models.len(), count + 1);
    models.retain(|model| model.model == "gpt-5.6-terra" || model.model == "picker-test-model");
    app.chat_widget.open_model_popup_with_presets(models);
    insta::assert_snapshot!(
        "model_picker_with_custom_provider",
        render_bottom_popup(&app.chat_widget, 100)
    );
    app.chat_widget
        .open_reasoning_popup(profiles[0].preset.clone());
    insta::assert_snapshot!(
        "custom_provider_reasoning",
        render_bottom_popup(&app.chat_widget, 100)
    );
    Ok(())
}
