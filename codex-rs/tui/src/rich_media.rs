//! Bounded iTerm images carried as private row markers through ordinary text layout.
//! Only registered markers produce trusted OSC escapes at the terminal writer.
//! Each image row is independent, so clipping, history insertion and reflow remain text operations.

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use image::DynamicImage;
use image::GenericImageView;
use image::ImageFormat;
use image::imageops::FilterType;
use std::collections::HashMap;
use std::io::Cursor;
use std::path::Path;
use std::process::Command;
use std::process::Stdio;
use std::sync::Arc;
use std::sync::LazyLock;
use std::sync::Mutex;

const FIRST_MARKER: u32 = 0xf0000;
const PADDING_MARKER: char = '\u{ffffe}';
const MAX_ASSETS: usize = 128;
const MAX_BYTES: usize = 24 * 1024 * 1024;
const MAX_ROWS: u32 = 20;
const CELL_W: u32 = 16;
const CELL_H: u32 = 32;

#[derive(Default)]
struct Cache {
    rendered: HashMap<String, Option<String>>,
    rows: Vec<Arc<str>>,
    bytes: usize,
}

static CACHE: LazyLock<Mutex<Cache>> = LazyLock::new(|| Mutex::new(Cache::default()));

pub(crate) fn enabled() -> bool {
    !cfg!(test)
        && std::env::var("CODEXY_RICH_MEDIA").as_deref() == Ok("1")
        && std::env::var("TERM_PROGRAM").as_deref() == Ok("iTerm.app")
        && std::env::var_os("TMUX").is_none()
        && std::env::var_os("ZELLIJ").is_none()
}

/// Returns an already-generated, trusted single-row image escape for a marker.
pub(crate) fn row(symbol: &str) -> Option<Arc<str>> {
    let mut chars = symbol.chars();
    let ch = chars.next()?;
    if ch == PADDING_MARKER && chars.next().is_none() {
        return Some(Arc::from(""));
    }
    if chars.next().is_some() || !(FIRST_MARKER..FIRST_MARKER + 65534).contains(&(ch as u32)) {
        return None;
    }
    CACHE
        .lock()
        .ok()?
        .rows
        .get((ch as u32 - FIRST_MARKER) as usize)
        .cloned()
}

pub(crate) fn latex(source: &str, display: bool, width: usize) -> Option<String> {
    if !enabled() || source.len() > 32768 {
        return None;
    }
    let foreground = crate::terminal_palette::default_fg().unwrap_or((216, 222, 233));
    let background = crate::terminal_palette::default_bg().unwrap_or((18, 26, 29));
    let key = format!("tex:{width}:{display}:{foreground:?}:{background:?}:{source}");
    cached(key, || {
        let helper = std::env::var_os("CODEXY_LATEX_RENDERER")?;
        let work = tempfile::tempdir().ok()?;
        let input = work.path().join("math.txt");
        let output = work.path().join("math.png");
        std::fs::write(&input, source).ok()?;
        let (r, g, b) = foreground;
        let (br, bg, bb) = background;
        let status = Command::new("python3")
            .arg(helper)
            .arg(&input)
            .arg(&output)
            .arg("--color")
            .arg(format!("{r:02X}{g:02X}{b:02X}"))
            .arg("--background")
            .arg(format!("{br:02X}{bg:02X}{bb:02X}"))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .ok()?;
        if !status.success() {
            tracing::debug!("LaTeX preview failed; preserving source rendering");
            return None;
        }
        Some((
            read_image(&output)?,
            width,
            if display { MAX_ROWS } else { 1 },
        ))
    })
}

pub(crate) fn local_image(path: &Path, width: usize) -> Option<String> {
    if !enabled() {
        return None;
    }
    let path = path.canonicalize().ok()?;
    let metadata = path.metadata().ok()?;
    let key = format!(
        "image:{width}:{}:{:?}:{}",
        path.display(),
        metadata.modified().ok(),
        metadata.len()
    );
    cached(key, || Some((read_image(&path)?, width, MAX_ROWS)))
}

fn read_image(path: &Path) -> Option<DynamicImage> {
    if !path.is_file() || path.metadata().ok()?.len() > 8 * 1024 * 1024 {
        return None;
    }
    let (w, h) = image::ImageReader::open(path)
        .ok()?
        .with_guessed_format()
        .ok()?
        .into_dimensions()
        .ok()?;
    if w == 0 || h == 0 || u64::from(w) * u64::from(h) > 8_000_000 {
        return None;
    }
    image::open(path).ok()
}

fn cached(
    key: String,
    render: impl FnOnce() -> Option<(DynamicImage, usize, u32)>,
) -> Option<String> {
    let mut cache = CACHE.lock().ok()?;
    if let Some(result) = cache.rendered.get(&key) {
        return result.clone();
    }
    if cache.rendered.len() >= MAX_ASSETS || cache.bytes >= MAX_BYTES {
        return None;
    }
    // The single lock serializes compilation and bounds the number of renderer children to one.
    let result =
        render().and_then(|(image, width, max_rows)| register(&mut cache, image, width, max_rows));
    cache.rendered.insert(key, result.clone());
    result
}

fn register(cache: &mut Cache, image: DynamicImage, width: usize, max_rows: u32) -> Option<String> {
    let max_cols = width.clamp(1, 120) as u32;
    let (w, h) = image.dimensions();
    let scale = (f64::from(max_cols * CELL_W) / f64::from(w))
        .min(f64::from(max_rows * CELL_H) / f64::from(h))
        .min(1.0);
    let w = (f64::from(w) * scale).round().max(1.0) as u32;
    let h = (f64::from(h) * scale).round().max(1.0) as u32;
    let columns = w.div_ceil(CELL_W);
    let rows = h.div_ceil(CELL_H);
    let image = image.resize_exact(w, h, FilterType::Triangle);
    let mut padded = image::RgbaImage::new(columns * CELL_W, rows * CELL_H);
    image::imageops::overlay(&mut padded, &image.to_rgba8(), 0, 0);
    let mut output = Vec::new();
    let mut payloads = Vec::new();
    for index in 0..rows {
        let stripe =
            image::imageops::crop_imm(&padded, 0, index * CELL_H, columns * CELL_W, CELL_H)
                .to_image();
        let mut png = Cursor::new(Vec::new());
        DynamicImage::ImageRgba8(stripe)
            .write_to(&mut png, ImageFormat::Png)
            .ok()?;
        let encoded = STANDARD.encode(png.into_inner());
        payloads.push(format!(
            "\x1b]1337;File=inline=1;width={columns};height=1;preserveAspectRatio=0:{encoded}\x07"
        ));
        let marker = char::from_u32(FIRST_MARKER + cache.rows.len() as u32 + index)?;
        output.push(format!(
            "{marker}{}",
            PADDING_MARKER
                .to_string()
                .repeat(columns.saturating_sub(1) as usize)
        ));
    }
    let bytes: usize = payloads.iter().map(String::len).sum();
    if cache.bytes + bytes > MAX_BYTES {
        return None;
    }
    cache.bytes += bytes;
    cache.rows.extend(payloads.into_iter().map(Arc::from));
    Some(output.join("\n"))
}

#[cfg(test)]
#[path = "rich_media_tests.rs"]
mod tests;
