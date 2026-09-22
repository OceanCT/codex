use super::*;
use pretty_assertions::assert_eq;

#[test]
fn rich_media_rows_are_bounded_and_reconstruct_png() {
    let mut cache = Cache::default();
    let image = DynamicImage::ImageRgba8(image::RgbaImage::from_pixel(
        320,
        160,
        image::Rgba([40, 150, 210, 255]),
    ));
    let markers = register(&mut cache, image, 10, 3).unwrap();
    assert_eq!(markers.lines().count(), 3);
    assert!(
        markers
            .lines()
            .all(|line| crate::width::display_width(line) == 10)
    );
    for row in &cache.rows {
        assert!(
            row.starts_with("\x1b]1337;File=inline=1;width=10;height=1;preserveAspectRatio=0:")
        );
        let payload = row.split_once(':').unwrap().1.trim_end_matches('\x07');
        let png = STANDARD.decode(payload).unwrap();
        let decoded = image::load_from_memory(&png).unwrap();
        assert_eq!(decoded.dimensions(), (160, 32));
    }
    insta::assert_snapshot!(format!(
        "image rows: {}\ncolumns: {}\nrow height: 1\nprotocol: OSC 1337\n",
        cache.rows.len(),
        crate::width::display_width(markers.lines().next().unwrap()),
    ));
}

#[test]
fn rich_media_budget_failure_does_not_register_partial_rows() {
    let mut cache = Cache {
        bytes: MAX_BYTES,
        ..Cache::default()
    };
    assert!(register(&mut cache, DynamicImage::new_rgba8(32, 32), 10, 2).is_none());
    assert!(cache.rows.is_empty());
}

#[test]
fn rich_media_rejects_unregistered_markers_and_control_sequences() {
    assert!(row("\x1b]1337;File=anything\x07").is_none());
    assert!(row("plain text").is_none());
    assert!(row("\u{ffffd}").is_none());
}
