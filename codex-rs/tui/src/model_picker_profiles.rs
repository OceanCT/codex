//! Explicit local provider profiles exposed alongside the server's model catalog.
//!
//! Only model settings are forwarded; permissions and unrelated profile settings stay unchanged.

use codex_protocol::openai_models::ModelPreset;
use codex_protocol::openai_models::ModelsResponse;
use color_eyre::eyre::Context;
use color_eyre::eyre::Result;
use color_eyre::eyre::bail;
use std::collections::HashMap;
use std::path::Path;

#[derive(Clone, Debug)]
pub(crate) struct PickerProfile {
    pub(crate) preset: ModelPreset,
    pub(crate) provider: String,
    pub(crate) overrides: HashMap<String, serde_json::Value>,
}

pub(crate) fn load(home: &Path) -> Result<Vec<PickerProfile>> {
    let manifest = home.join("model-picker-profiles.json");
    if !manifest.exists() {
        return Ok(Vec::new());
    }
    let names: Vec<String> = serde_json::from_slice(&std::fs::read(manifest)?)?;
    let mut profiles = Vec::new();
    for name in names {
        if name.is_empty()
            || !name
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
        {
            bail!("Invalid model picker profile name");
        }
        let document: toml::Value = toml::from_str(&std::fs::read_to_string(
            home.join(format!("{name}.config.toml")),
        )?)
        .wrap_err("Invalid model picker profile")?;
        let field = |key| {
            document
                .get(key)
                .and_then(toml::Value::as_str)
                .ok_or_else(|| color_eyre::eyre::eyre!("Profile {name} is missing {key}"))
        };
        let model = field("model")?;
        let provider = field("model_provider")?.to_string();
        let catalog_path = home.join(field("model_catalog_json")?);
        let catalog: ModelsResponse = serde_json::from_slice(&std::fs::read(&catalog_path)?)?;
        let info = catalog
            .models
            .into_iter()
            .find(|info| info.slug == model)
            .ok_or_else(|| {
                color_eyre::eyre::eyre!("Model {model} is absent from profile catalog")
            })?;
        if profiles
            .iter()
            .any(|profile: &PickerProfile| profile.preset.model == model)
        {
            bail!("Duplicate model picker profile for {model}");
        }
        let mut overrides = HashMap::new();
        for key in ["model_providers", "web_search", "service_tier"] {
            if let Some(value) = document.get(key) {
                overrides.insert(key.to_string(), serde_json::to_value(value)?);
            }
        }
        overrides.insert(
            "model_catalog_json".to_string(),
            serde_json::to_value(catalog_path)?,
        );
        profiles.push(PickerProfile {
            preset: info.into(),
            provider,
            overrides,
        });
    }
    Ok(profiles)
}

pub(crate) fn extend(models: &mut Vec<ModelPreset>, profiles: &[PickerProfile]) {
    for profile in profiles {
        if !models
            .iter()
            .any(|model| model.model == profile.preset.model)
        {
            models.push(profile.preset.clone());
        }
    }
}

pub(crate) fn needs_provider_selection(home: &Path, current: &str, selected: &str) -> bool {
    match load(home) {
        Ok(profiles) => profiles
            .iter()
            .any(|profile| profile.preset.model == current || profile.preset.model == selected),
        // Surface invalid profile configuration instead of sending its model to the default API.
        Err(_) => true,
    }
}
