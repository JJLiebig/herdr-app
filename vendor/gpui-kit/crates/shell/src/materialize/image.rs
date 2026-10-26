//! HERDR: image data and textures live only while their source is rendered.

use std::sync::Arc;

use gpui::{ImageCacheProvider as _, ImageSource, SharedString};

pub(super) fn source(path: &str) -> ImageSource {
    let ImageSource::Resource(resource) = ImageSource::from(path) else {
        unreachable!("a path is an image resource")
    };
    let key = SharedString::from(path.to_owned());
    ImageSource::Custom(Arc::new(move |window, cx| {
        let view = window.current_view();
        window.with_element_namespace(("shell-image", view), |window| {
            gpui::retain_all(key.clone())
                .provide(window, cx)
                .load(&resource, window, cx)
        })
    }))
}

#[cfg(test)]
#[path = "image/tests.rs"]
mod tests;
